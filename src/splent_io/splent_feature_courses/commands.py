"""CLI commands contributed by the courses feature.

Exposed under the ``feature:courses`` group, e.g.::

    splent feature:courses new-course 2026/2027
    splent feature:courses copy-course 2627 --from egc-2025-2026
    splent feature:courses status
    splent feature:courses publish egc-2026-2027/tema-1
    splent feature:courses hide egc-2026-2027/examen

They run inside the active product's Flask app context, so they reach the
product database through the same service the web pages use and can never
disagree with it about who sees what.

``status`` is the one staff run most. It answers, from a terminal and in
one line per item, the question this wiki exists for, which is whether the
exam is still withheld.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
from flask import current_app

from splent_io.splent_feature_courses.services import local_timezone
from splent_framework.services.service_locator import service_proxy

courses_service = service_proxy("CoursesService")

# What a reference can name. Only needed on the command line when a
# category and a page in the same course happen to share a slug.
KINDS = ("course", "category", "page")

# Width of the name column in the status walk, wide enough for a page
# title and its slug before the state is pushed to the right.
NAME_WIDTH = 56


# ── Academic years ───────────────────────────────────────────────────────

YEAR_LONG = re.compile(r"^(\d{4})\s*[/-]\s*(\d{4})$")
YEAR_SHORT = re.compile(r"^(\d{2})(\d{2})$")


def _parse_year(value: str) -> str:
    """Normalise an academic year to its long form, 2026/2027.

    Both forms staff already write are accepted, the long one and the four
    digit shorthand they use for folders and mail subjects. The two halves
    must be consecutive years, which turns the usual typo into a refusal
    rather than into a course nobody notices is called 2026/2028.
    """
    value = (value or "").strip()
    match = YEAR_LONG.match(value)
    if match:
        start, end = int(match.group(1)), int(match.group(2))
    else:
        match = YEAR_SHORT.match(value)
        if not match:
            raise click.ClickException(
                f"'{value}' is not an academic year. Write it as 2026/2027 or as 2627."
            )
        start = 2000 + int(match.group(1))
        # The shorthand carries no century, so the second half is read as
        # the next turn of the same one.
        end = start - start % 100 + int(match.group(2))
        if end < start:
            end += 100
    if end != start + 1:
        raise click.ClickException(
            f"An academic year spans two consecutive years, so {start}/{end} "
            f"looks like a typo. Did you mean {start}/{start + 1}?"
        )
    return f"{start}/{end}"


def _year_fields(year: str) -> dict[str, str]:
    """What a configured template may substitute."""
    start, end = year.split("/")
    return {"year": year, "start": start, "end": end, "short": start[2:] + end[2:]}


def _fill(template: str, year: str, config_key: str) -> str:
    """Put the year into a template a product configured."""
    try:
        return template.format(**_year_fields(year))
    except (IndexError, KeyError, ValueError) as exc:
        raise click.ClickException(
            f"{config_key} could not be filled in ({exc}). A year offers "
            "{year}, {start}, {end} and {short}."
        ) from exc


def _course_name(year: str) -> str:
    """What this product calls the year, from COURSES_NAME_PREFIX.

    A prefix holding a placeholder decides the whole name, and a plain one
    is put in front of the year. Either way the word naming the subject
    comes from the product's configuration, never from here.
    """
    prefix = current_app.config.get("COURSES_NAME_PREFIX") or ""
    if "{" in prefix:
        return _fill(prefix, year, "COURSES_NAME_PREFIX").strip()
    return f"{prefix.rstrip()} {year}".strip()


def _course_description(year: str) -> str:
    template = current_app.config.get("COURSES_DESCRIPTION_TEMPLATE") or ""
    if not template:
        return ""
    return _fill(template, year, "COURSES_DESCRIPTION_TEMPLATE")


def _category_names(categories: str | None, empty: bool) -> list[str]:
    """The categories a new course starts with."""
    if empty and categories:
        raise click.ClickException("Use either --empty or --categories, not both.")
    if empty:
        return []
    if categories is not None:
        names = [name.strip() for name in categories.split(",") if name.strip()]
        if not names:
            raise click.ClickException(
                "--categories held no names. Pass --empty to start with none."
            )
        return names
    return list(current_app.config.get("COURSES_DEFAULT_CATEGORIES") or [])


# ── Moments ──────────────────────────────────────────────────────────────


def _zone():
    """The timezone staff read release dates in, from COURSES_TIMEZONE.

    A lab at 08:00 means 08:00 where the course is taught, so a date shown
    in UTC is a date read wrong for half the year.
    """
    name = local_timezone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        click.secho(
            f"  COURSES_TIMEZONE is '{name}', which this machine does not "
            "know. Showing UTC instead.",
            fg="yellow",
        )
        return timezone.utc


def _as_utc(moment: datetime | None) -> datetime | None:
    """MySQL hands back naive datetimes even from an aware column."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _stamp(moment: datetime, zone) -> str:
    return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


# ── References ───────────────────────────────────────────────────────────


def _no_such_course(slug: str) -> str:
    slugs = [course.slug for course in courses_service.repository.list_newest_first()]
    if not slugs:
        return f"No course called '{slug}'. There are no courses yet."
    shown = ", ".join(slugs[:8])
    more = f" and {len(slugs) - 8} more" if len(slugs) > 8 else ""
    return f"No course called '{slug}'. The courses are {shown}{more}."


def _resolve(reference: str, kind: str | None):
    """The item a reference names, and the course it belongs to.

    Slugs repeat across years, because every year has a tema-1, so a bare
    slug names four different pages and is not an answer. References are
    therefore scoped to their course, as course-slug for the course itself
    and course-slug/child-slug for what is inside it.
    """
    parts = [part for part in (reference or "").strip("/").split("/") if part]
    if not parts or len(parts) > 2:
        raise click.ClickException(
            f"'{reference}' is not a reference. Write course-slug, "
            "course-slug/category-slug or course-slug/page-slug. Run status "
            "to see them."
        )

    course = courses_service.course_by_slug(parts[0])
    if course is None:
        raise click.ClickException(_no_such_course(parts[0]))

    if len(parts) == 1:
        if kind is not None and kind != "course":
            raise click.ClickException(
                f"'{reference}' names a course, so it cannot be a {kind}."
            )
        return "course", course, course

    slug = parts[1]
    if kind == "course":
        raise click.ClickException(
            f"A course reference is just its slug, so drop the '/{slug}'."
        )

    category = (
        courses_service.category_by_slug(course, slug)
        if kind in (None, "category")
        else None
    )
    page = (
        courses_service.page_by_slug(course, slug) if kind in (None, "page") else None
    )

    if category is not None and page is not None:
        # Nothing stops a course holding a category and a page under the
        # same slug, and guessing which one staff meant is how the wrong
        # thing gets published.
        raise click.ClickException(
            f"'{reference}' is both a category and a page in {course.name}. "
            "Say which with --kind category or --kind page."
        )
    if category is not None:
        return "category", category, course
    if page is not None:
        return "page", page, course

    raise click.ClickException(
        f"{course.name} holds no {kind or 'category or page'} called "
        f"'{slug}'. Run status {course.slug} to see what it holds."
    )


def _label(kind: str, item) -> str:
    return f"{kind.capitalize()} {item.name}"


# ── new-course ───────────────────────────────────────────────────────────


@click.command("new-course")
@click.argument("year")
@click.option(
    "--categories",
    default=None,
    help="Comma separated category names, in place of the configured "
    "COURSES_DEFAULT_CATEGORIES.",
)
@click.option(
    "--empty",
    is_flag=True,
    help="Start the year with no categories at all.",
)
def new_course(year, categories, empty):
    """Start an academic year, hidden.

    YEAR is written as 2026/2027 or as 2627. The name, the description and
    the categories come from the product's configuration, which is what
    lets the same command start a year for any subject.

    The course is born hidden, so staff can fill next year while this year
    is still the one students read. Publish it when it is ready.
    """
    year = _parse_year(year)
    name = _course_name(year)
    description = _course_description(year)
    names = _category_names(categories, empty)

    try:
        course = courses_service.create_course(
            name=name, description=description, categories=names
        )
    except ValueError as exc:
        raise click.ClickException(f"{exc} Nothing was created.") from exc

    created = courses_service.categories.list_for_course(course.id)

    click.echo()
    click.secho(f"  Created {course.name}", fg="green")
    click.echo(f"    slug        {course.slug}")
    click.echo("    hidden      yes, students see nothing of it yet")
    if description:
        click.echo(f"    description {description}")
    click.echo(f"    categories  {len(created)}")
    for category in created:
        click.echo(f"      {category.slug:<28} {category.name}")
    click.echo()
    click.echo("  Release it when it is ready with")
    click.echo(f"      splent feature:courses publish {course.slug}")
    click.echo()


# ── copy-course ──────────────────────────────────────────────────────────


@click.command("copy-course")
@click.argument("year")
@click.option(
    "--from",
    "source_ref",
    default=None,
    help="Course to copy, by slug. Defaults to the newest one.",
)
def copy_course(year, source_ref):
    """Duplicate a course into a new year, everything withheld.

    The pages that repeat every September are copied rather than shared,
    so editing the new year never changes what a past year shows. Nothing
    arrives released, release dates included, because last year's schedule
    would otherwise publish this year's material on days already gone.

    YEAR is written as 2026/2027 or as 2627. Without --from the newest
    course is the one copied, which is the September case.
    """
    year = _parse_year(year)
    name = _course_name(year)

    if source_ref:
        source = courses_service.course_by_slug(source_ref)
        if source is None:
            raise click.ClickException(_no_such_course(source_ref))
    else:
        source = courses_service.newest_course()
        if source is None:
            raise click.ClickException(
                "There are no courses to copy. Start one with new-course."
            )

    source_name = source.name
    source_pages = len(courses_service.pages.list_for_course(source.id))

    try:
        course = courses_service.copy_course(source, name)
    except ValueError as exc:
        raise click.ClickException(f"{exc} Nothing was copied.") from exc

    # The copy inherits the description of the year it came from, which
    # names that year. Rewriting it from the template keeps the new course
    # describing itself.
    description = _course_description(year)
    rewritten = bool(description) and description != course.description
    if rewritten:
        courses_service.repository.update(course.id, description=description)

    categories = courses_service.categories.list_for_course(course.id)
    pages = courses_service.pages.list_for_course(course.id)

    click.echo()
    click.secho(f"  Copied {source_name} into {course.name}", fg="green")
    click.echo(f"    slug        {course.slug}")
    click.echo(f"    categories  {len(categories)}")
    click.echo(f"    pages       {len(pages)}")
    click.echo("    hidden      yes, the copy and everything inside it")
    if rewritten:
        click.echo(f"    description {description}")

    skipped = source_pages - len(pages)
    if skipped > 0:
        # Pages hanging off no category are not part of a category walk,
        # so they stay behind and staff should know rather than discover it
        # in October.
        click.secho(
            f"    {_plural(skipped, 'page', 'pages')} in no category "
            f"stayed behind in {source_name}.",
            fg="yellow",
        )
    click.echo()
    click.echo("  Release it when it is ready with")
    click.echo(f"      splent feature:courses publish {course.slug}")
    click.echo()


# ── status ───────────────────────────────────────────────────────────────


def _describe(item, note: str, now: datetime, zone) -> tuple[str, str, str]:
    """How one item reads, in which colour, and which tally it falls in.

    The state is the item's own, so a page released inside a hidden
    category says so rather than claiming to be visible, and the note
    names whichever parent is holding it back.
    """
    if item.hidden:
        return "hidden", "red", "withheld"
    released_at = _as_utc(item.publish_at)
    if released_at is not None and released_at > now:
        return f"scheduled {_stamp(released_at, zone)}", "yellow", "scheduled"
    if note:
        return f"released, {note}", "yellow", "withheld"
    return "visible", "green", "visible"


def _print_row(depth: int, item, note: str, now: datetime, zone, tally: dict) -> None:
    text, colour, bucket = _describe(item, note, now, zone)
    tally[bucket] += 1
    label = f"{'  ' * depth}{item.name} ({item.slug})"
    click.echo(f"  {label:<{NAME_WIDTH}} " + click.style(text, fg=colour))


@click.command("status")
@click.argument("course_ref", metavar="[COURSE]", required=False)
def status(course_ref):
    """What a student sees right now, and what is scheduled.

    Walks every course, every category and every page and marks each one
    visible, hidden or scheduled with the moment it opens, in the timezone
    the course is taught in. Nothing here runs on a clock, so what this
    prints is what the next request will answer.

    COURSE limits the walk to one course, by slug.
    """
    now = datetime.now(timezone.utc)
    zone = _zone()

    if course_ref:
        course = courses_service.course_by_slug(course_ref)
        if course is None:
            raise click.ClickException(_no_such_course(course_ref))
        walk = [course]
    else:
        walk = courses_service.repository.list_newest_first()

    if not walk:
        click.secho("  No courses yet. Start one with new-course.", fg="yellow")
        return

    counts = {"course": 0, "category": 0, "page": 0}
    tally = {"visible": 0, "withheld": 0, "scheduled": 0}

    click.echo()
    click.echo(f"  Right now is {_stamp(now, zone)}.")
    click.echo()

    for course in walk:
        counts["course"] += 1
        course_open = courses_service.course_visible(course, None, now)
        _print_row(0, course, "", now, zone, tally)

        for category in courses_service.categories.list_for_course(course.id):
            counts["category"] += 1
            note = "" if course_open else "withheld by its course"
            _print_row(1, category, note, now, zone, tally)
            category_open = courses_service.category_visible(category, None, now)

            for page in courses_service.pages.list_for_category(category.id):
                counts["page"] += 1
                if not course_open:
                    page_note = "withheld by its course"
                elif not category_open:
                    page_note = "withheld by its category"
                else:
                    page_note = ""
                _print_row(2, page, page_note, now, zone, tally)

        loose = [
            page
            for page in courses_service.pages.list_for_course(course.id)
            if page.category_id is None
        ]
        if loose:
            click.echo("    (no category)")
            for page in loose:
                counts["page"] += 1
                note = "" if course_open else "withheld by its course"
                _print_row(2, page, note, now, zone, tally)

        click.echo()

    click.echo(
        f"  {_plural(counts['course'], 'course', 'courses')}, "
        f"{_plural(counts['category'], 'category', 'categories')}, "
        f"{_plural(counts['page'], 'page', 'pages')}."
    )
    click.echo(
        f"  A student sees {tally['visible']} of them right now. "
        f"{tally['withheld']} withheld, {tally['scheduled']} scheduled."
    )
    click.echo()


# ── publish and hide ─────────────────────────────────────────────────────


def _report_reach(kind: str, item, course) -> None:
    """Say when something just published is still out of reach.

    Publishing acts on the item named and on nothing else, so a page
    inside a hidden category is released and still invisible. Saying which
    parent is responsible is the difference between one more command and
    an afternoon of confusion.

    The outermost parent is the one named, the same order status walks in,
    so both answer with the same word for the same situation.
    """
    now = datetime.now(timezone.utc)
    if kind == "course":
        return
    if kind == "category":
        if courses_service.category_visible(item, None, now):
            return
        blocker, reference = "its course", course.slug
    else:
        if courses_service.page_visible(item, None, now):
            return
        if not item.course.is_released(now) or item.category is None:
            blocker, reference = "its course", course.slug
        else:
            blocker = "its category"
            reference = f"{course.slug}/{item.category.slug}"

    click.secho(f"  Students still cannot see it, {blocker} is withheld.", fg="yellow")
    click.echo(f"      splent feature:courses publish {reference}")


def _travels_with(kind: str, item) -> str:
    """How much a hidden course or category takes with it."""
    if kind == "course":
        categories = len(courses_service.categories.list_for_course(item.id))
        pages = len(courses_service.pages.list_for_course(item.id))
        if not categories and not pages:
            return ""
        return (
            f"{_plural(categories, 'category', 'categories')} and "
            f"{_plural(pages, 'page', 'pages')}"
        )
    if kind == "category":
        pages = len(courses_service.pages.list_for_category(item.id))
        return _plural(pages, "page", "pages") if pages else ""
    return ""


@click.command("publish")
@click.argument("reference")
@click.option(
    "--kind",
    type=click.Choice(KINDS),
    default=None,
    help="Which one is meant, for the rare course holding a category and a "
    "page under the same slug.",
)
def publish(reference, kind):
    """Make something visible to students right now.

    REFERENCE is course-slug, course-slug/category-slug or
    course-slug/page-slug, scoped to its course because every year has a
    tema-1 and a bare slug would name several of them.

    Any release date is cleared, since a date still in the future would
    keep withholding what was just published. Only the item named is
    published, and this says so when a parent is still holding it back.
    """
    kind_name, item, course = _resolve(reference, kind)
    was_hidden = item.hidden
    scheduled_for = _as_utc(item.publish_at)
    zone = _zone()

    courses_service.set_visibility(item, hidden=False, publish_at=None)

    changed = []
    if was_hidden:
        changed.append("it is no longer hidden")
    if scheduled_for is not None:
        changed.append(f"its release date {_stamp(scheduled_for, zone)} is gone")

    click.echo()
    if changed:
        click.secho(
            f"  {_label(kind_name, item)} is published, {' and '.join(changed)}.",
            fg="green",
        )
    else:
        click.secho(
            f"  {_label(kind_name, item)} was already published. Nothing changed.",
            fg="yellow",
        )
    _report_reach(kind_name, item, course)
    click.echo()


@click.command("hide")
@click.argument("reference")
@click.option(
    "--kind",
    type=click.Choice(KINDS),
    default=None,
    help="Which one is meant, for the rare course holding a category and a "
    "page under the same slug.",
)
def hide(reference, kind):
    """Withhold something from students right now.

    REFERENCE is course-slug, course-slug/category-slug or
    course-slug/page-slug.

    Everything below goes with it, so hiding a category withholds its
    pages without touching them one by one. What is withheld answers 404
    rather than 403, indistinguishable from material that was never
    written, which is the whole point of hiding it.

    Any release date is kept and has no effect while this is hidden, so
    the schedule staff set is still there when it is published again.
    """
    kind_name, item, course = _resolve(reference, kind)
    was_hidden = item.hidden
    scheduled_for = _as_utc(item.publish_at)
    zone = _zone()

    courses_service.set_visibility(item, hidden=True)

    click.echo()
    if was_hidden:
        click.secho(
            f"  {_label(kind_name, item)} was already hidden. Nothing changed.",
            fg="yellow",
        )
    else:
        click.secho(
            f"  {_label(kind_name, item)} is hidden. Students get a 404 for it.",
            fg="green",
        )
        below = _travels_with(kind_name, item)
        if below:
            click.echo(f"  {below} went with it.")
    if scheduled_for is not None:
        click.echo(
            f"  Its release date {_stamp(scheduled_for, zone)} is kept and has "
            "no effect while it is hidden."
        )
    click.echo()


cli_commands = [new_course, copy_course, status, publish, hide]
