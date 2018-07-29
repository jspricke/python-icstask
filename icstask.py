# Python library to convert between Taskwarrior and iCalendar
#
# Copyright (C) 2015-2018  Jochen Sprickerhof
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
"""Python library to convert between Taskwarrior and iCalendar"""

from datetime import datetime, time, timedelta, timezone
from dateutil import rrule
from json import dumps, loads
from os.path import basename, expanduser, getmtime, join
from re import findall
from socket import getfqdn
from subprocess import PIPE, run
from tzlocal import get_localzone
from threading import Lock
from vobject import iCalendar, readOne


class IcsTask:
    """Represents a collection of Tasks"""

    def __init__(self, data_location=expanduser('~/.task'), localtz=None):
        """Constructor

        data_location -- Path to the Taskwarrior data directory
        """
        self._data_location = data_location
        self._localtz = localtz if localtz else get_localzone()
        self._lock = Lock()
        self._mtime = 0
        self._tasks = {}
        self._update()

    def _update(self):
        """Reload Taskwarrior files if the mtime is newer"""
        update = False

        with self._lock:
            for fname in ['pending.data', 'completed.data']:
                mtime = getmtime(join(self._data_location, fname))
                if mtime > self._mtime:
                    self._mtime = mtime
                    update = True

            if update:
                tasklist = loads(run(['task', 'rc.verbose=nothing', 'rc.hooks=off', f'rc.data.location={self._data_location}', 'export'], stdout=PIPE).stdout.decode('utf-8'))
                for task in tasklist:
                    project = task['project'] if 'project' in task else 'unaffiliated'
                    if project not in self._tasks:
                        self._tasks[project] = {}
                    self._tasks[project][task['uuid']] = task

    def _gen_uid(self, uuid):
        return '{}@{}'.format(uuid, getfqdn())

    def _ics_datetime(self, string):
        dt = datetime.strptime(string, '%Y%m%dT%H%M%SZ')
        return dt.replace(tzinfo=timezone.utc).astimezone(self._localtz)

    def _tw_timestamp(self, dt):
        if not isinstance(dt, datetime):
            dt = datetime.combine(dt, time.min)
        return dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

    def to_vobject_etag(self, project, uid):
        """Return iCal object and etag of one Taskwarrior entry

        project -- the Taskwarrior project
        uid -- the UID of the task
        """
        self._update()

        vtodos = iCalendar()
        project = basename(project)
        uid = uid.split('@')[0]
        self._gen_vtodo(self._tasks[project][uid], vtodos.add('vtodo'))
        return vtodos, '"%s"' % self._tasks[project][uid]['modified']

    def to_vobject(self, project=None, uid=None):
        """Return vObject object of Taskwarrior tasks
        If filename and UID are specified, the vObject only contains that task.
        If only a filename is specified, the vObject contains all events in the project.
        Otherwise the vObject contains all all objects of all files associated with the IcsTask object.

        project -- the Taskwarrior project
        uid -- the UID of the task
        """
        self._update()
        vtodos = iCalendar()

        if uid:
            uid = uid.split('@')[0]
            if not project:
                for p in self._tasks:
                    if uid in self._tasks[p]:
                        project = p
                        break
            self._gen_vtodo(self._tasks[basename(project)][uid], vtodos.add('vtodo'))
        elif project:
            for task in self._tasks[basename(project)].values():
                self._gen_vtodo(task, vtodos.add('vtodo'))
        else:
            for project in self._tasks:
                for task in self._tasks[project].values():
                    self._gen_vtodo(task, vtodos.add('vtodo'))

        return vtodos

    def _gen_vtodo(self, task, vtodo):
        vtodo.add('uid').value = self._gen_uid(task['uuid'])
        vtodo.add('dtstamp').value = self._ics_datetime(task['entry'])

        if 'modified' in task:
            vtodo.add('last-modified').value = self._ics_datetime(task['modified'])

        if 'start' in task:
            vtodo.add('dtstart').value = self._ics_datetime(task['start'])

        if 'due' in task:
            due = self._ics_datetime(task['due'])
            if due.time() == time():
                vtodo.add('due').value = due.date()
            else:
                vtodo.add('due').value = due

        if 'end' in task:
            vtodo.add('completed').value = self._ics_datetime(task['end'])

        vtodo.add('summary').value = task['description']

        if 'tags' in task:
            vtodo.add('categories').value = task['tags']

        if 'priority' in task:
            if task['priority'] == 'H':
                vtodo.add('priority').value = '1'
            elif task['priority'] == 'M':
                vtodo.add('priority').value = '5'
            elif task['priority'] == 'L':
                vtodo.add('priority').value = '9'

        if task['status'] == 'pending' or task['status'] == 'waiting':
            if 'start' in task:
                vtodo.add('status').value = 'IN-PROCESS'
            else:
                vtodo.add('status').value = 'NEEDS-ACTION'
        elif task['status'] == 'completed':
            vtodo.add('status').value = 'COMPLETED'
        elif task['status'] == 'deleted':
            vtodo.add('status').value = 'CANCELLED'

        if 'annotations' in task:
            vtodo.add('description').value = '\n'.join([annotation['description'] for annotation in task['annotations']])

        if 'recur' in task and task['recur'] == '7days':
            rset = rrule.rruleset()
            rset.rrule(rrule.rrule(freq=rrule.WEEKLY))
            vtodo.rruleset = rset

    def to_task(self, vtodo, project=None, uuid=None):
        """Add or modify a task from vTodo to Taskwarrior
        vtodo -- the vTodo to add
        project -- the project to add (see get_filesnames() as well)
        uuid -- the UID of the task in Taskwarrior
        """
        task = {}

        if project and project != 'all_projects' and project != 'unaffiliated':
            task['project'] = project

        if uuid:
            task['uuid'] = uuid

        if hasattr(vtodo, 'dtstamp'):
            task['entry'] = self._tw_timestamp(vtodo.dtstamp.value)

        if hasattr(vtodo, 'last_modified'):
            task['modified'] = self._tw_timestamp(vtodo.last_modified.value)

        if hasattr(vtodo, 'dtstart'):
            task['start'] = self._tw_timestamp(vtodo.dtstart.value)

        if hasattr(vtodo, 'due'):
            task['due'] = self._tw_timestamp(vtodo.due.value)

        if hasattr(vtodo, 'completed'):
            task['end'] = self._tw_timestamp(vtodo.completed.value)

        task['description'] = vtodo.summary.value

        if hasattr(vtodo, 'categories'):
            task['tags'] = vtodo.categories.value

        if hasattr(vtodo, 'priority'):
            priority = int(vtodo.priority.value)
            if priority < 3:
                task['priority'] = 'H'
            elif 3 < priority < 7:
                task['priority'] = 'M'
            else:
                task['priority'] = 'L'

        if hasattr(vtodo, 'description'):
            task['annotations'] = []
            for delta, comment in enumerate(vtodo.description.value.split('\n')):
                # Hack because Taskwarrior import doesn't accept multiple annotations with the same timestamp
                stamp = self._tw_timestamp(vtodo.dtstamp.value + timedelta(seconds=delta))
                if uuid in self._tasks.get(project, {}) and 'annotations' in self._tasks[project][uuid]:
                    for annotation in self._tasks[project][uuid]['annotations']:
                        if annotation['description'] == comment:
                            stamp = annotation['entry']
                            break
                task['annotations'].append({'entry': stamp, 'description': comment})

        if hasattr(vtodo, 'status'):
            if vtodo.status.value == 'IN-PROCESS':
                task['status'] = 'pending'
                if 'start' not in task:
                    task['start'] = self._tw_timestamp(vtodo.dtstamp.value)
            elif vtodo.status.value == 'NEEDS-ACTION':
                task['status'] = 'pending'
            elif vtodo.status.value == 'COMPLETED':
                task['status'] = 'completed'
                if 'end' not in task:
                    task['end'] = self._tw_timestamp(vtodo.dtstamp.value)
            elif vtodo.status.value == 'CANCELLED':
                task['status'] = 'deleted'
                if 'end' not in task:
                    task['end'] = self._tw_timestamp(vtodo.dtstamp.value)

        json = dumps(task, separators=(',', ':'), sort_keys=True)
        with self._lock:
            p = run(['task', 'rc.verbose=nothing', 'rc.recurrence.confirmation=no', f'rc.data.location={self._data_location}', 'import', '-'], input=json, encoding='utf-8', stdout=PIPE)
        uuid = findall('(?:add|mod)  ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) ', p.stdout)[0]
        self._update()
        return self._gen_uid(uuid)

    def get_filesnames(self):
        """Returns a list of all Taskwarrior projects as virtual files in the data directory"""
        self._update()
        projects = list(self._tasks.keys()) + ['all_projects', 'unaffiliated']
        return [join(self._data_location, p.split()[0]) for p in projects]

    def get_uids(self, project=None):
        """Return a list of UIDs
        project -- the Project to filter for
        """
        self._update()

        if not project or project.endswith('all_projects'):
            return [self._gen_uid(task['uuid']) for project in self._tasks for task in self._tasks[project].values()]

        return [self._gen_uid(uuid) for uuid in self._tasks[basename(project)]]

    def get_meta(self):
        """Meta tags of the vObject collection"""
        return {'tag': 'VCALENDAR', 'C:supported-calendar-component-set': 'VTODO'}

    def last_modified(self):
        """Last time this Taskwarrior files where parsed"""
        self._update()
        return self._mtime

    def append_vobject(self, vtodo, project=None):
        """Add a task from vObject to Taskwarrior
        vtodo -- the iCalendar to add
        project -- the project to add (see get_filesnames() as well)
        """
        if project:
            project = basename(project)
        return self.to_task(vtodo.vtodo, project)

    def remove(self, uuid, project=None):
        """Remove a task from Taskwarrior
        uuid -- the UID of the task
        project -- not used
        """
        uuid = uuid.split('@')[0]
        with self._lock:
            run(['task', 'rc.verbose=nothing', f'rc.data.location={self._data_location}', 'rc.confirmation=no', uuid, 'delete'])

    def replace_vobject(self, uuid, vtodo, project=None):
        """Update the task with the UID from the vObject
        uuid -- the UID of the task
        vtodo -- the iCalendar to add
        project -- the project to add (see get_filesnames() as well)
        """
        self._update()
        uuid = uuid.split('@')[0]
        if project:
            project = basename(project)
        return self.to_task(vtodo.vtodo, project, uuid)


def task2ics():
    """Command line tool to convert from Taskwarrior to iCalendar"""
    from argparse import ArgumentParser, FileType
    from sys import stdout

    parser = ArgumentParser(description='Converter from Taskwarrior to iCalendar syntax.')
    parser.add_argument('indir', nargs='?', help='Input Taskwarrior directory (default to ~/.task)', default=expanduser('~/.task'))
    parser.add_argument('outfile', nargs='?', type=FileType('w'), default=stdout,
                        help='Output iCalendar file (default: stdout)')
    args = parser.parse_args()

    task = IcsTask(args.indir)
    args.outfile.write(task.to_vobject().serialize())


def ics2task():
    """Command line tool to convert from iCalendar to Taskwarrior"""
    from argparse import ArgumentParser, FileType
    from sys import stdin

    parser = ArgumentParser(description='Converter from iCalendar to Taskwarrior syntax.')
    parser.add_argument('infile', nargs='?', type=FileType('r'), default=stdin,
                        help='Input iCalendar file (default: stdin)')
    parser.add_argument('outdir', nargs='?', help='Output Taskwarrior directory (default to ~/.task)', default=expanduser('~/.task'))
    args = parser.parse_args()

    vobject = readOne(args.infile.read())
    task = IcsTask(args.outdir)
    for todo in vobject.vtodo_list:
        task.to_task(todo)
