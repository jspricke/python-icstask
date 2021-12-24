# Python library to convert between Taskwarrior and iCalendar
#
# Copyright (C) 2015-2021  Jochen Sprickerhof
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

from datetime import datetime, time, timedelta, timezone
from json import dumps, loads
from os.path import basename, exists, getmtime, join
from re import findall
from socket import getfqdn
from subprocess import check_call, check_output
from threading import Lock
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from dateutil import rrule
from vobject import iCalendar
from vobject.base import Component, readOne


class IcsTask:
    """Represents a collection of Tasks."""

    def __init__(
        self,
        data_location: str = "",
        localtz: Optional[ZoneInfo] = None,
        task_projects: list[str] = None,
        start_task: bool = True,
    ) -> None:
        """Constructor.

        data_location -- Path to the Taskwarrior data directory
        """
        if not data_location:
            out = check_output(["task", "rc.confirmation=no", "_show"], text=True)
            self._data_location = findall("data.location=(.*)", out)[0]
        else:
            self._data_location = data_location
        self._localtz = localtz if localtz else ZoneInfo("localtime")
        self._task_projects = task_projects if task_projects else []
        self._start_task = start_task
        self._lock = Lock()
        self._mtime = 0.0
        self._tasks: dict[str, dict[str, Any]] = {}
        self._update()

    def _update(self) -> None:
        """Reload Taskwarrior files if the mtime is newer."""
        update = False

        with self._lock:
            for fname in ["pending.data", "completed.data"]:
                data_file = join(self._data_location, fname)
                if exists(data_file):
                    mtime = getmtime(data_file)
                    if mtime > self._mtime:
                        self._mtime = mtime
                        update = True

            if update:
                self._tasks = {}
                out = check_output(
                    [
                        "task",
                        "rc.verbose=nothing",
                        "rc.hooks=off",
                        f"rc.data.location={self._data_location}",
                        "export",
                    ],
                    text=True,
                )
                tasklist = loads(out)
                for task in tasklist:
                    project = task["project"] if "project" in task else "unaffiliated"
                    if project not in self._tasks:
                        self._tasks[project] = {}
                    self._tasks[project][task["uuid"]] = task

    @staticmethod
    def _gen_uid(uuid: str) -> str:
        return f"{uuid}@{getfqdn()}"

    def _ics_datetime(self, string: str) -> datetime:
        dtime = datetime.strptime(string, "%Y%m%dT%H%M%SZ")
        return dtime.replace(tzinfo=timezone.utc).astimezone(self._localtz)

    @staticmethod
    def _tw_timestamp(dtime: datetime) -> str:
        if not isinstance(dtime, datetime):
            dtime = datetime.combine(dtime, time.min)
        return dtime.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def to_vobject_etag(self, project: str, uid: str) -> tuple[Component, str]:
        """Return iCal object and etag of one Taskwarrior entry.

        project -- the Taskwarrior project
        uid -- the UID of the task
        """
        return self.to_vobjects(project, [uid])[0][1:3]

    def to_vobjects(
        self, filename: str, uids: Iterable[str] = None
    ) -> list[tuple[str, Component, str]]:
        """Return iCal objects and etags of all Taskwarrior entries in uids.

        filename -- the Taskwarrior project
        uids -- the UIDs of the Taskwarrior tasks (all if None)
        """
        self._update()

        if not uids:
            uids = self.get_uids(filename)

        project = basename(filename)
        if project == "all_projects":
            tasks = {
                uuid: tasks[uuid] for tasks in self._tasks.values() for uuid in tasks
            }
        else:
            if project not in self._tasks:
                return []
            tasks = self._tasks[project]

        items = []

        for uid in uids:
            vtodos = iCalendar()
            uuid = uid.split("@")[0]
            self._gen_vtodo(tasks[uuid], vtodos.add("vtodo"))
            items.append((uid, vtodos, f'"{tasks[uuid]["modified"]}"'))
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
        self._update()
        vtodos = iCalendar()

        tasks = []
        if uid:
            uid = uid.split("@")[0]
            if not project:
                for pro, tsks in self._tasks.items():
                    if uid in tsks:
                        project = pro
                        break
            tasks.append(self._tasks[basename(project)][uid])
        elif project:
            tasks = list(self._tasks[basename(project)].values())
        else:
            for tsks in self._tasks.values():
                tasks.extend(tsks.values())

        for task in tasks:
            # skip recurring instances in favor of the single parent task
            if task.get("recur") and task.get("parent"):
                continue
            self._gen_vtodo(task, vtodos.add("vtodo"))

        return vtodos

    @staticmethod
    def _create_rset(recur: str, freq: int, postfix: str) -> rrule.rruleset:
        rset = rrule.rruleset()
        rset.rrule(rrule.rrule(freq=freq, interval=int(recur[: -len(postfix)])))
        return rset

    def _gen_vtodo(self, task: dict[str, Any], vtodo: Component) -> None:
        vtodo.add("uid").value = IcsTask._gen_uid(task["uuid"])
        vtodo.add("dtstamp").value = self._ics_datetime(task["entry"])

        if "modified" in task:
            vtodo.add("last-modified").value = self._ics_datetime(task["modified"])

        if "start" in task:
            vtodo.add("dtstart").value = self._ics_datetime(task["start"])

        if "due" in task:
            due = self._ics_datetime(task["due"])
            if due.time() == time():
                vtodo.add("due").value = due.date()
            else:
                vtodo.add("due").value = due

        if "end" in task:
            vtodo.add("completed").value = self._ics_datetime(task["end"])

        vtodo.add("summary").value = task["description"]

        if "tags" in task:
            vtodo.add("categories").value = task["tags"]

        if "priority" in task:
            if task["priority"] == "H":
                vtodo.add("priority").value = "1"
            elif task["priority"] == "M":
                vtodo.add("priority").value = "5"
            elif task["priority"] == "L":
                vtodo.add("priority").value = "9"

        if task["status"] == "pending" or task["status"] == "waiting":
            if "start" in task:
                vtodo.add("status").value = "IN-PROCESS"
            else:
                vtodo.add("status").value = "NEEDS-ACTION"
        elif task["status"] == "completed":
            vtodo.add("status").value = "COMPLETED"
        elif task["status"] == "deleted":
            vtodo.add("status").value = "CANCELLED"

        if "annotations" in task:
            vtodo.add("description").value = "\n".join(
                [annotation["description"] for annotation in task["annotations"]]
            )

        if "recur" in task:
            if task["recur"] == "weekly":
                rset = rrule.rruleset()
                rset.rrule(rrule.rrule(freq=rrule.WEEKLY))
                vtodo.rruleset = rset
            elif task["recur"].endswith("days"):
                vtodo.rruleset = IcsTask._create_rset(
                    task["recur"], rrule.DAILY, "days"
                )
            elif task["recur"].endswith("w"):
                vtodo.rruleset = IcsTask._create_rset(task["recur"], rrule.WEEKLY, "w")
            elif task["recur"].endswith("week"):
                vtodo.rruleset = IcsTask._create_rset(
                    task["recur"], rrule.WEEKLY, "week"
                )
            elif task["recur"].endswith("weeks"):
                vtodo.rruleset = IcsTask._create_rset(
                    task["recur"], rrule.WEEKLY, "weeks"
                )
            elif task["recur"].endswith("mo"):
                vtodo.rruleset = IcsTask._create_rset(
                    task["recur"], rrule.MONTHLY, "mo"
                )
            elif task["recur"].endswith("month"):
                vtodo.rruleset = IcsTask._create_rset(
                    task["recur"], rrule.MONTHLY, "month"
                )
            elif task["recur"].endswith("months"):
                vtodo.rruleset = IcsTask._create_rset(
                    task["recur"], rrule.MONTHLY, "months"
                )
            elif task["recur"].endswith("y"):
                vtodo.rruleset = IcsTask._create_rset(task["recur"], rrule.YEARLY, "y")
            elif task["recur"].endswith("year"):
                vtodo.rruleset = IcsTask._create_rset(
                    task["recur"], rrule.YEARLY, "year"
                )
            elif task["recur"].endswith("years"):
                vtodo.rruleset = IcsTask._create_rset(
                    task["recur"], rrule.YEARLY, "years"
                )
            else:
                raise ValueError(f'Unsupported recurrence string {task["recur"]}')

    def to_task(self, vtodo: Component, project: str = "", uuid: str = "") -> str:
        """Add or modify a task from vTodo to Taskwarrior.

        vtodo -- the vTodo to add
        project -- the project to add (see get_filesnames() as well)
        uuid -- the UID of the task in Taskwarrior
        """
        task: dict[str, Any] = {}

        if project and project != "all_projects" and project != "unaffiliated":
            task["project"] = project

        if uuid:
            task["uuid"] = uuid

        if hasattr(vtodo, "dtstamp"):
            task["entry"] = IcsTask._tw_timestamp(vtodo.dtstamp.value)

        if hasattr(vtodo, "last_modified"):
            task["modified"] = IcsTask._tw_timestamp(vtodo.last_modified.value)

        if hasattr(vtodo, "dtstart"):
            task["start"] = IcsTask._tw_timestamp(vtodo.dtstart.value)

        if hasattr(vtodo, "due"):
            task["due"] = IcsTask._tw_timestamp(vtodo.due.value)

        if hasattr(vtodo, "completed"):
            task["end"] = IcsTask._tw_timestamp(vtodo.completed.value)

        task["description"] = vtodo.summary.value

        if hasattr(vtodo, "categories"):
            task["tags"] = vtodo.categories.value

        if hasattr(vtodo, "priority"):
            priority = int(vtodo.priority.value)
            if priority < 3:
                task["priority"] = "H"
            elif 3 < priority < 7:
                task["priority"] = "M"
            else:
                task["priority"] = "L"

        if hasattr(vtodo, "description"):
            task["annotations"] = []
            for delta, comment in enumerate(vtodo.description.value.split("\n")):
                # Hack because Taskwarrior import doesn't accept multiple
                # annotations with the same timestamp
                stamp = IcsTask._tw_timestamp(
                    vtodo.dtstamp.value + timedelta(seconds=delta)
                )
                if (
                    uuid in self._tasks.get(project, {})
                    and "annotations" in self._tasks[project][uuid]
                ):
                    for annotation in self._tasks[project][uuid]["annotations"]:
                        if annotation["description"] == comment:
                            stamp = annotation["entry"]
                            break
                task["annotations"].append({"entry": stamp, "description": comment})

        if hasattr(vtodo, "status"):
            if vtodo.status.value == "IN-PROCESS":
                task["status"] = "pending"
                if self._start_task and "start" not in task:
                    task["start"] = IcsTask._tw_timestamp(vtodo.dtstamp.value)
            elif vtodo.status.value == "NEEDS-ACTION":
                task["status"] = "pending"
            elif vtodo.status.value == "COMPLETED":
                task["status"] = "completed"
                if "end" not in task:
                    task["end"] = IcsTask._tw_timestamp(vtodo.dtstamp.value)
            elif vtodo.status.value == "CANCELLED":
                task["status"] = "deleted"
                if "end" not in task:
                    task["end"] = IcsTask._tw_timestamp(vtodo.dtstamp.value)

        json = dumps(task, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
        with self._lock:
            out = check_output(
                [
                    "task",
                    "rc.verbose=nothing",
                    "rc.recurrence.confirmation=no",
                    f"rc.data.location={self._data_location}",
                    "import",
                    "-",
                ],
                input=json,
                text=True,
            )
        uuid = findall(
            "(?:add|mod)  ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) ",
            out,
        )[0]
        self._update()
        return IcsTask._gen_uid(uuid)

    def get_filesnames(self) -> list[str]:
        """Return a list of all Taskwarrior projects as virtual files in the data directory."""
        self._update()
        projects = set(
            list(self._tasks.keys())
            + self._task_projects
            + ["all_projects", "unaffiliated"]
        )
        return [join(self._data_location, p.split()[0]) for p in projects]

    def get_uids(self, project: str = "") -> list[str]:
        """Return a list of UIDs.

        project -- the Project to filter for
        """
        self._update()

        if not project or project.endswith("all_projects"):
            return [
                IcsTask._gen_uid(task["uuid"])
                for tasks in self._tasks.values()
                for task in tasks.values()
            ]

        if basename(project) not in self._tasks:
            return []

        return [IcsTask._gen_uid(uuid) for uuid in self._tasks[basename(project)]]

    @staticmethod
    def get_meta() -> dict[str, str]:
        """Meta tags of the vObject collection."""
        return {"tag": "VCALENDAR", "C:supported-calendar-component-set": "VTODO"}

    def last_modified(self) -> float:
        """Last time this Taskwarrior files where parsed."""
        self._update()
        return self._mtime

    def append_vobject(self, vtodo: Component, project: str = "") -> str:
        """Add a task from vObject to Taskwarrior.

        vtodo -- the iCalendar to add
        project -- the project to add (see get_filesnames() as well)
        """
        if project:
            project = basename(project)
        return self.to_task(vtodo.vtodo, project)

    def remove(self, uuid: str, project: str = "") -> None:
        """Remove a task from Taskwarrior.

        uuid -- the UID of the task
        project -- not used
        """
        uuid = uuid.split("@")[0]
        with self._lock:
            check_call(
                [
                    "task",
                    "rc.verbose=nothing",
                    f"rc.data.location={self._data_location}",
                    "rc.confirmation=no",
                    uuid,
                    "delete",
                ]
            )

    def replace_vobject(self, uuid: str, vtodo: Component, project: str = "") -> str:
        """Update the task with the UID from the vObject.

        uuid -- the UID of the task
        vtodo -- the iCalendar to add
        project -- the project to add (see get_filesnames() as well)
        """
        self._update()
        uuid = uuid.split("@")[0]
        if project:
            project = basename(project)
        return self.to_task(vtodo.vtodo, project, uuid)

    def move_vobject(self, uuid: str, from_project: str, to_project: str) -> None:
        """Update the project of the task with the UID uuid."""
        if to_project not in self.get_filesnames():
            return

        uuid = uuid.split("@")[0]
        with self._lock:
            check_call(
                [
                    "task",
                    "rc.verbose=nothing",
                    f"rc.data.location={self._data_location}",
                    "rc.confirmation=no",
                    uuid,
                    "modify",
                    f"project:{basename(to_project)}",
                ]
            )


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
        type=FileType("w"),
        default=stdout,
        help="Output iCalendar file (default: stdout)",
    )
    args = parser.parse_args()

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
        type=FileType("r"),
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
    for todo in vobject.vtodo_list:
        task.to_task(todo)
