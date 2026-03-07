# Python library to convert between Taskwarrior and iCalendar
#
# Copyright (C) 2015-2024  Jochen Sprickerhof
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""Python library to convert between Taskwarrior and iCalendar."""

from collections.abc import Iterable
from datetime import datetime, time, timedelta, timezone
from os.path import basename, exists, getmtime, join
from re import match, search
from socket import getfqdn
from subprocess import check_output
from threading import Lock
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from dateutil import rrule, tz
from taskchampion import Annotation, Operations, Replica, Status, Tag, Task
from vobject import iCalendar
from vobject.base import Component, readOne


class IcsTask:
    """Represents a collection of Tasks."""

    def __init__(
        self,
        data_location: str = "",
        localtz: None | ZoneInfo = None,
        task_projects: list[str] | None = None,
        start_task: bool = True,
        fqdn: str | None = None,
    ) -> None:
        """Constructor.

        data_location -- Path to the Taskwarrior data directory
        """
        if data_location:
            self._data_location = data_location
        else:
            out = check_output(["task", "rc.confirmation=no", "_show"], text=True)
            if group := search(r"data.location=(.*)", out):
                self._data_location = group[1]
            else:
                raise ValueError("task data location not found")
        self._localtz = localtz or tz.gettz()
        self._task_projects = task_projects or []
        self._start_task = start_task
        self._fqdn = fqdn or getfqdn()

    def _gen_uid(self, uuid: str) -> str:
        return f"{uuid}@{self._fqdn}"

    def to_vobject_etag(self, project: str, uid: str) -> tuple[Component, str]:
        """Return iCal object and etag of one Taskwarrior entry.

        project -- the Taskwarrior project
        uid -- the UID of the task
        """
        return self.to_vobjects(project, [uid])[0][1:3]

    def to_vobjects(
        self, project: str = "", uids: Iterable[str] | None = None
    ) -> list[tuple[str, Component, str]]:
        """Return iCal objects and etags of all Taskwarrior entries in uids.

        project -- the Taskwarrior project
        uids -- the UIDs of the Taskwarrior tasks (all if None)
        """
        project = basename(project)
        replica = Replica.new_on_disk(self._data_location, True)
        if not project or project == "all_projects":
            tasks = {
                self._gen_uid(uuid): task for uuid, task in replica.all_tasks().items()
            }
        else:
            tasks = {
                self._gen_uid(uuid): task for uuid, task in replica.all_tasks().items()
                if task.get_value("project") ==  project
            }

        if not uids:
            uids = tasks.keys()

        items = []

        for uid in uids:
            task = tasks[uid]
            # skip recurring instances in favor of the single parent task
            if task.get_status == Status.Recurring and task.get_value("parent"):
                continue
            vtodos = iCalendar()
            self._gen_vtodo(task, uid, vtodos.add("vtodo"))
            items.append((uid, vtodos, f'"{task.get_modified()}"'))
        return items

    def to_vobject(self, project: str = "", uid: str = "") -> Component:
        """Return vObject object of Taskwarrior tasks.

        If filename and UID are specified, the vObject only contains that task.
        If only a filename is specified, the vObject contains all events in the project.
        Otherwise the vObject contains all all objects of all files associated
        with the IcsTask object.

        project -- the Taskwarrior project
        uid -- the UID of the task
        """
        ical = iCalendar()
        for _, vtodos, _ in self.to_vobjects():
            for vtodo in vtodos.vtodo_list:
                ical.add(vtodo)
        return ical

    def _gen_vtodo(self, task: Task, uid: str, vtodo: Component) -> None:
        vtodo.add("uid").value = uid
        vtodo.add("dtstamp").value = task.get_entry().astimezone(self._localtz)

        if last := task.get_modified():
            vtodo.add("last-modified").value = last.astimezone(self._localtz)

        if start := task.get_value("start"):
            vtodo.add("dtstart").value = datetime.fromtimestamp(int(start), self._localtz)

        if due := task.get_due():
            due = due.astimezone(self._localtz)
            if due.time() == time():
                vtodo.add("due").value = due.date()
            else:
                vtodo.add("due").value = due

        if end := task.get_value("end"):
            vtodo.add("completed").value = datetime.fromtimestamp(int(end), self._localtz)

        vtodo.add("summary").value = task.get_description()

        if tags := [str(tag) for tag in task.get_tags() if tag.is_user()]:
            vtodo.add("categories").value = tags

        match task.get_priority():
            case "H":
                vtodo.add("priority").value = "1"
            case "M":
                vtodo.add("priority").value = "5"
            case "L":
                vtodo.add("priority").value = "9"

        match task.get_status():
            case Status.Pending:
                if task.is_active():
                    vtodo.add("status").value = "IN-PROCESS"
                else:
                    vtodo.add("status").value = "NEEDS-ACTION"
            case Status.Completed:
                vtodo.add("status").value = "COMPLETED"
            case Status.Deleted:
                vtodo.add("status").value = "CANCELLED"

        if annotations := task.get_annotations():
            annotations.sort(key=lambda annotation: annotation.entry)
            vtodo.add("description").value = "\n".join(
                [annotation.description for annotation in annotations]
            )

        if recur := task.get_value("recur"):
            if group := match(r"(\d*)\s*(\w*)", recur):
                interval = group[1]
                frequency = group[2]
            else:
                raise ValueError(f"Unsupported recurrence string {recur}")
            rset = rrule.rruleset()
            match frequency:
                case "daily":
                    rset.rrule(rrule.rrule(freq=rrule.DAILY))
                case "weekly":
                    rset.rrule(rrule.rrule(freq=rrule.WEEKLY))
                case "monthly":
                    rset.rrule(rrule.rrule(freq=rrule.MONTHLY))
                case "yearly":
                    rset.rrule(rrule.rrule(freq=rrule.YEARLY))
                case "days":
                    rset.rrule(rrule.rrule(freq=rrule.DAILY, interval=int(interval)))
                case ("w" | "week" | "weeks"):
                    rset.rrule(rrule.rrule(freq=rrule.WEEKLY, interval=int(interval)))
                case ("mo" | "month" | "months"):
                    rset.rrule(rrule.rrule(freq=rrule.MONTHLY, interval=int(interval)))
                case ("y" | "year" | "years"):
                    rset.rrule(rrule.rrule(freq=rrule.YEARLY, interval=int(interval)))
                case _:
                    raise ValueError(f"Unsupported recurrence string {recur}")
            vtodo.rruleset = rset

    def to_tasks(self, ical: iCalendar) -> None:
        """Add or modify a task from vTodo to Taskwarrior.

        ical -- iCalendar with vTodos to add
        """
        ops = Operations()
        replica = Replica.new_on_disk(self._data_location, True)

        for vtodo in ical.vtodo_list:
            task = replica.create_task(str(uuid4()), ops)
            self._to_task(vtodo, task, ops)

        replica.commit_operations(ops)

    def _to_task(self, vtodo: Component, task: Task, ops: Operations, project: str = "") -> None:
        """Convert a vTodo to Taskwarrior Operations.

        vtodo -- the vTodo to add
        task -- Taskwarrior Task
        ops -- Taskwarrior Operations to be committed
        project -- the project to add (see get_filesnames() as well)
        """
        if project and project != "all_projects" and project != "unaffiliated":
            task.set_value("project", project, ops)
        elif task.get_value("project"):
            task.set_value("project", None, ops)

        if hasattr(vtodo, "dtstamp"):
            task.set_entry(vtodo.dtstamp.value.astimezone(timezone.utc), ops)

        if hasattr(vtodo, "last_modified"):
            task.set_modified(vtodo.last_modified.value.astimezone(timezone.utc), ops)

        if hasattr(vtodo, "dtstart"):
            task.set_value("start", str(int(vtodo.dtstart.value.timestamp())), ops)
        elif task.get_value("start"):
            task.set_value("start", None, ops)

        if hasattr(vtodo, "due"):
            due = vtodo.due.value
            if not isinstance(due, datetime):
                due = datetime.combine(due, time.min)
            task.set_due(due.astimezone(timezone.utc), ops)
        elif task.get_due():
            task.set_due(None, ops)

        if hasattr(vtodo, "completed"):
            task.set_value("end", str(int(vtodo.completed.value.timestamp())), ops)
        elif task.get_value("end"):
            task.set_value("end", None, ops)

        task.set_description(vtodo.summary.value, ops)

        if hasattr(vtodo, "categories"):
            categories = set(vtodo.categories.value)
        else:
            categories = set()
        tags = {str(tag) for tag in task.get_tags() if tag.is_user()}
        for tag in tags - categories:
            task.remove_tag(Tag(tag), ops)
        for category in categories - tags:
            task.add_tag(Tag(category), ops)

        if hasattr(vtodo, "priority"):
            priority = int(vtodo.priority.value)
            if priority <= 3:
                task.set_priority("H", ops)
            elif 3 < priority < 7:
                task.set_priority("M", ops)
            else:
                task.set_priority("L", ops)

        if hasattr(vtodo, "description"):
            descriptions = vtodo.description.value.split("\n")
        else:
            descriptions = []
        annotations = task.get_annotations()
        annotations.sort(key=lambda annotation: annotation.entry)
        annotations = {annotation.description: annotation.entry for annotation in annotations}
        if annotations:
            for annotation in annotations:
                if annotation not in descriptions:
                    task.remove_annotation(annotations[annotation], ops)
        if descriptions:
            # Hack because Taskwarrior import doesn't accept multiple
            # annotations with the same timestamp
            for delta, description in enumerate(descriptions):
                if description not in annotations:
                    stamp = vtodo.dtstamp.value + timedelta(seconds=delta)
                    task.add_annotation(Annotation(stamp.astimezone(timezone.utc), description), ops)

        if hasattr(vtodo, "status"):
            match vtodo.status.value:
                case "IN-PROCESS":
                    task.set_status(Status.Pending, ops)
                    if self._start_task and not task.get_value("start"):
                        task.set_value("start", str(int(vtodo.dtstamp.value.stamp())), ops)
                case "NEEDS-ACTION":
                    task.set_status(Status.Pending, ops)
                case "COMPLETED":
                    task.set_status(Status.Completed, ops)
                    if not task.get_value("end"):
                        task.set_value("end", str(int(vtodo.completed.value.timestamp())), ops)
                case "CANCELLED":
                    task.set_status(Status.Deleted, ops)
                    if not task.get_value("end"):
                        task.set_value("end", str(int(vtodo.completed.value.timestamp())), ops)
        else:
            task.set_status(Status.Pending, ops)

    def get_filesnames(self) -> list[str]:
        """Return a list of all Taskwarrior projects as virtual files in the data directory."""
        replica = Replica.new_on_disk(self._data_location, True)
        projects = {task.get_value("project") or "unaffiliated" for task in replica.all_tasks().values()}
        projects.update(self._task_projects)
        projects.update(("all_projects", "unaffiliated"))
        return [join(self._data_location, project) for project in sorted(projects)]

    def get_uids(self, project: str = "") -> list[str]:
        """Return a list of UIDs.

        project -- the Project to filter for
        """
        project = basename(project)
        replica = Replica.new_on_disk(self._data_location, True)

        if not project or project == "all_projects":
            return [self._gen_uid(uuid) for uuid in replica.all_task_uuids() ]

        return [
            self._gen_uid(uuid)
            for uuid, task in replica.all_tasks().items() if
            task.get_value("project") ==  project
        ]

    @staticmethod
    def get_meta() -> dict[str, str]:
        """Meta tags of the vObject collection."""
        return {
            "tag": "VCALENDAR",
            "C:supported-calendar-component-set": "VTODO",
        }

    def last_modified(self) -> float:
        """Last time this Taskwarrior files where parsed."""
        return getmtime(join(self._data_location, "taskchampion.sqlite3"))

    def append_vobject(self, vtodo: Component, project: str = "") -> str:
        """Add a task from vObject to Taskwarrior.

        vtodo -- the iCalendar to add
        project -- the project to add (see get_filesnames() as well)
        """
        ops = Operations()
        replica = Replica.new_on_disk(self._data_location, True)
        uuid = str(uuid4())
        task = replica.create_task(uuid, ops)
        self._to_task(vtodo.vtodo, task, ops, basename(project))
        replica.commit_operations(ops)
        return self._gen_uid(uuid)

    def remove(self, uuid: str, _project: str = "") -> None:
        """Remove a task from Taskwarrior.

        uuid -- the UID of the task
        project -- not used
        """
        ops = Operations()
        replica = Replica.new_on_disk(self._data_location, True)
        task = replica.get_task(uuid.split("@")[0])
        task.set_status(Status.Deleted, ops)
        replica.commit_operations(ops)

    def replace_vobject(self, uid: str, vtodo: Component, project: str = "") -> str:
        """Update the task with the UID from the vObject.

        uid -- the UID of the task
        vtodo -- the iCalendar to add
        project -- the project to add (see get_filesnames() as well)
        """
        ops = Operations()
        replica = Replica.new_on_disk(self._data_location, True)
        task = replica.get_task(uid.split("@")[0])
        self._to_task(vtodo.vtodo, task, ops, basename(project))
        replica.commit_operations(ops)
        return uid

    def move_vobject(self, uid: str, _from_project: str, to_project: str) -> None:
        """Update the project of the task with the UID uuid."""
        if to_project not in self.get_filesnames():
            return

        ops = Operations()
        replica = Replica.new_on_disk(self._data_location, True)
        task = replica.get_task(uid.split("@")[0])
        if to_project != "all_projects" and to_project != "unaffiliated":
            task.set_value("project", to_project, ops)
        elif task.get_value("project"):
            task.set_value("project", None, ops)
        replica.commit_operations(ops)


def task2ics() -> None:
    """Command line tool to convert from Taskwarrior to iCalendar."""
    from argparse import ArgumentParser, FileType
    from sys import stdout

    parser = ArgumentParser(
        description="Converter from Taskwarrior to iCalendar syntax."
    )
    parser.add_argument(
        "indir",
        nargs="?",
        help="Input Taskwarrior directory (autodetect by default)",
        default="",
    )
    parser.add_argument(
        "outfile",
        nargs="?",
        type=FileType("w", encoding="utf-8"),
        default=stdout,
        help="Output iCalendar file (default: stdout)",
    )
    args = parser.parse_args()

    if args.indir and args.indir != "-" and not exists(args.indir):
        args.outfile = open(args.indir, "w", encoding="utf-8")
        args.indir = None

    task = IcsTask(args.indir)
    args.outfile.write(task.to_vobject().serialize())


def ics2task() -> None:
    """Command line tool to convert from iCalendar to Taskwarrior."""
    from argparse import ArgumentParser, FileType
    from sys import stdin

    parser = ArgumentParser(
        description="Converter from iCalendar to Taskwarrior syntax."
    )
    parser.add_argument(
        "infile",
        nargs="?",
        type=FileType("r", encoding="utf-8"),
        default=stdin,
        help="Input iCalendar file (default: stdin)",
    )
    parser.add_argument(
        "outdir",
        nargs="?",
        help="Output Taskwarrior directory (autodetect by default)",
        default="",
    )
    args = parser.parse_args()

    vobject = readOne(args.infile.read())
    task = IcsTask(args.outdir)
    task.to_tasks(vobject)
