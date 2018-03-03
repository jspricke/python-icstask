# Python library to convert between Taskwarrior and iCalendar
#
# Copyright (C) 2015-2017  Jochen Sprickerhof
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

from datetime import timedelta
from dateutil import parser, rrule
from dateutil.tz import tzutc
from json import dumps, loads
from os.path import basename, expanduser, getmtime, join
from re import findall
from socket import getfqdn
from subprocess import PIPE, run
from threading import Lock
from vobject import iCalendar, readOne


class IcsTask:
    """Represents a collection of Tasks"""

    def __init__(self, data_location=expanduser('~/.task')):
        """Constructor

        data_location -- Path to the Taskwarrior data folder
        """
        self._data_location = data_location
        self._lock = Lock()
        self._mtime = 0
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
                self._tasks = loads(run(['task', 'rc.verbose=nothing', 'rc.hooks=off', f'rc.data.location={self._data_location}', 'export'], stdout=PIPE).stdout.decode('utf-8'))

    def _gen_uid(self, task):
        return '{}@{}'.format(task['uuid'], getfqdn())

    def _annotation_timestamp(self, uuid, description, dtstamp, delta):
        task = [task for task in self._tasks if task['uuid'] == uuid]
        if len(task) == 1:
            for annotation in task[0]['annotations']:
                if annotation['description'] == description:
                    return annotation['entry']
        # Hack because task import doesn't accept multiple annotations with the same timestamp
        dtstamp += timedelta(seconds=delta)
        return dtstamp.astimezone(tzutc()).strftime('%Y%m%dT%H%M%SZ')

    def to_vobject(self, project=None, uid=None):
        """Return vObject object of Taskwarrior tasks
        If filename and UID are specified, the vObject only contains that task.
        If only a filename is specified, the vObject contains all events in the project.
        Otherwise the vObject contains all all objects of all files associated with the IcsTask object.

        project -- the Taskwarrior project
        uid -- the UID of the task
        """
        self._update()
        todos = iCalendar()

        tasks = self._tasks
        if uid:
            uid = uid.split('@')[0]
            tasks = [task for task in self._tasks if task['uuid'] == uid]
        elif project:
            tasks = [task for task in self._tasks if task['project'] == basename(project)]

        for task in tasks:
            vtodo = todos.add('vtodo')

            vtodo.add('uid').value = self._gen_uid(task)
            vtodo.add('dtstamp').value = parser.parse(task['entry'])

            if 'modified' in task:
                vtodo.add('last-modified').value = parser.parse(task['modified'])

            if 'start' in task:
                vtodo.add('dtstart').value = parser.parse(task['start'])

            if 'due' in task:
                vtodo.add('due').value = parser.parse(task['due'])

            if 'end' in task:
                vtodo.add('completed').value = parser.parse(task['end'])

            vtodo.add('summary').value = task['description']

            if 'tags' in task:
                vtodo.add('categories').value = ','.join(task['tags'])

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
        return todos

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
            task['entry'] = vtodo.dtstamp.value.astimezone(tzutc()).strftime('%Y%m%dT%H%M%SZ')

        if hasattr(vtodo, 'last_modified'):
            task['modified'] = vtodo.last_modified.value.astimezone(tzutc()).strftime('%Y%m%dT%H%M%SZ')

        if hasattr(vtodo, 'dtstart'):
            task['start'] = vtodo.dtstart.value.astimezone(tzutc()).strftime('%Y%m%dT%H%M%SZ')

        if hasattr(vtodo, 'due'):
            task['due'] = vtodo.due.value.astimezone(tzutc()).strftime('%Y%m%dT%H%M%SZ')

        if hasattr(vtodo, 'completed'):
            task['end'] = vtodo.completed.value.astimezone(tzutc()).strftime('%Y%m%dT%H%M%SZ')

        task['description'] = vtodo.summary.value

        if hasattr(vtodo, 'categories'):
            task['tags'] = vtodo.categories.value.split(',')

        if hasattr(vtodo, 'priority'):
            priority = int(vtodo.priority.value)
            if priority < 3:
                task['priority'] = 'H'
            elif 3 < priority < 7:
                task['priority'] = 'M'
            else:
                task['priority'] = 'L'

        if hasattr(vtodo, 'description'):
            task['annotations'] = [{'entry': self._annotation_timestamp(uuid, comment, vtodo.dtstamp.value, delta), 'description': comment} for delta, comment in enumerate(vtodo.description.value.split('\n'))]

        if hasattr(vtodo, 'status'):
            if vtodo.status.value == 'IN-PROCESS':
                task['status'] = 'pending'
                if 'start' not in task:
                    task['start'] = vtodo.dtstamp.value.astimezone(tzutc()).strftime('%Y%m%dT%H%M%SZ')
            elif vtodo.status.value == 'NEEDS-ACTION':
                task['status'] = 'pending'
            elif vtodo.status.value == 'COMPLETED':
                task['status'] = 'completed'
                if 'end' not in task:
                    task['end'] = vtodo.dtstamp.value.astimezone(tzutc()).strftime('%Y%m%dT%H%M%SZ')
            elif vtodo.status.value == 'CANCELLED':
                task['status'] = 'deleted'
                if 'end' not in task:
                    task['end'] = vtodo.dtstamp.value.astimezone(tzutc()).strftime('%Y%m%dT%H%M%SZ')

        json = dumps(task, separators=(',', ':'), sort_keys=True)
        with self._lock:
            p = run(['task', 'rc.verbose=nothing', 'rc.recurrence.confirmation=no', f'rc.data.location={self._data_location}', 'import', '-'], input=json, encoding='utf-8', stdout=PIPE)
        uuid = findall('(?:add|mod)  ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) ', p.stdout)[0]
        self._update()
        task = next((task for task in self._tasks if task['uuid'] == uuid))
        return self._gen_uid(task)

    def get_filesnames(self):
        """Returns a list of all Taskwarrior projects as virtual files in the data folder"""
        self._update()
        projects = set([task['project'] for task in self._tasks if 'project' in task])
        projects = list(projects) + ['all_projects', 'unaffiliated']
        return [join(self._data_location, p.split()[0]) for p in projects]

    def get_uids(self, project=None):
        """Return a list of UIDs
        project -- the Project to filter for
        """
        self._update()
        tasks = self._tasks
        if project:
            project = basename(project)
            if project == 'all_projects':
                pass
            elif project == 'unaffiliated':
                tasks = [task for task in self._tasks if 'project' not in task]
            else:
                tasks = [task for task in self._tasks if 'project' in task and task['project'] == project]

        return [self._gen_uid(task) for task in tasks]

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
    parser.add_argument('infile', nargs='?', help='Input Taskwarrior folder (default to ~/task)', default=expanduser('~/.task'))
    parser.add_argument('outfile', nargs='?', type=FileType('w'), default=stdout,
                        help='Output iCalendar file (default: stdout)')
    args = parser.parse_args()

    task = IcsTask(args.infile)
    args.outfile.write(task.to_vobject().serialize())


def ics2task():
    """Command line tool to convert from iCalendar to Taskwarrior"""
    from argparse import ArgumentParser, FileType
    from sys import stdin

    parser = ArgumentParser(description='Converter from iCalendar to Taskwarrior syntax.')
    parser.add_argument('infile', nargs='?', type=FileType('r'), default=stdin,
                        help='Input iCalendar file (default: stdin)')
    parser.add_argument('outfile', nargs='?', help='Output Taskwarrior folder (default to ~/task)', default=expanduser('~/.task'))
    args = parser.parse_args()

    vobject = readOne(args.infile.read())
    task = IcsTask(args.outfile)
    for todo in vobject.vtodo_list:
        task.to_task(todo)
