# -----------------------------------------------------------------------------
# Getting Things GNOME! - a personal organizer for the GNOME desktop
# Copyright (c) 2008-2013 - Lionel Dricot & Bertrand Rousseau
# Copyright (c) 2023 - odoood
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <http://www.gnu.org/licenses/>.
# -----------------------------------------------------------------------------
from collections import deque
import threading
import logging

from GTG.backends.backend_signals import BackendSignals

log = logging.getLogger(__name__)

class TaskSource():
    """
    Transparent interface between the real backend and the DataStore.
    Is in charge of connecting and disconnecting to signals
    """

    def __init__(self, requester, backend, datastore):
        """
        Instantiates a TaskSource object.

        @param requester: a Requester
        @param backend:  the backend being wrapped
        @param datastore: a Datastore
        """
        self.backend = backend
        self.req = requester
        self.backend.register_datastore(datastore)
        self.tasktree = datastore.get_tasks_tree().get_main_view()
        self.to_set = deque()
        self.to_remove = deque()
        self.please_quit = False
        self.task_filter = self.get_task_filter_for_backend()
        if log.isEnabledFor(logging.DEBUG):
            self.timer_timestep = 5
        else:
            self.timer_timestep = 1
        self.add_task_handle = None
        self.set_task_handle = None
        self.remove_task_handle = None
        self.to_set_timer = None

    def start_get_tasks(self):
        """ Loads all task from the backend and connects its signals
        afterwards. """
        self.backend.start_get_tasks()
        self._connect_signals()
        if self.backend.is_default():
            BackendSignals().default_backend_loaded()

    def get_task_filter_for_backend(self):
        """
        Filter that checks if the task should be stored in this backend.

        @returns function: a function that accepts a task and returns
                 True/False whether the task should be stored or not
        """

        def backend_filter(req, task, parameters):
            """
            Filter that checks if two tags sets intersect. It is used to check
            if a task should be stored inside a backend
            @param task: a task object
            @param tags_to_match_set: a *set* of tag names
            """
            try:
                tags_to_match_set = parameters['tags']
            except KeyError:
                return []
            all_tasks_tag = req.get_alltag_tag().get_name()
            if all_tasks_tag in tags_to_match_set:
                return True
            task_tags = set(task.get_tags_name())
            return task_tags.intersection(tags_to_match_set)

        attached_tags = self.backend.get_attached_tags()
        return lambda task: backend_filter(self.requester, task,
                                           {"tags": set(attached_tags)})

    def should_task_id_be_stored(self, task_id):
        """
        Helper function:  Checks if a task should be stored in this backend

        @param task_id: a task id
        @returns bool: True if the task should be stored
        """
        # task = self.req.get_task(task_id)
        # FIXME: it will be a lot easier to add, instead,
        # a filter to a tree and check that this task is well in the tree
        #return self.task_filter(task)
        return True

    def queue_set_task(self, tid, path=None):
        """
        Updates the task in the DataStore.  Actually, it adds the task to a
        queue to be updated asynchronously.

        @param task: The Task object to be updated.
        @param path: its path in TreeView widget => not used there
        """
        if self.should_task_id_be_stored(tid):
            if tid not in self.to_set and tid not in self.to_remove:
                self.to_set.appendleft(tid)
                self.__try_launch_setting_thread()
        else:
            self.queue_remove_task(tid, path)

    def launch_setting_thread(self, bypass_please_quit=False):
        """
        Operates the threads to set and remove tasks.
        Releases the lock when it is done.

        @param bypass_please_quit: if True, the self.please_quit
                                   "quit condition" is ignored. Currently,
                                   it's turned to true after the quit
                                   condition has been issued, to execute
                                   eventual pending operations.
        """
        while not self.please_quit or bypass_please_quit:
            try:
                tid = self.to_set.pop()
            except IndexError:
                break
            # we check that the task is not already marked for deletion
            # and that it's still to be stored in this backend
            # NOTE: no need to lock, we're reading
            if tid not in self.to_remove and \
                    self.should_task_id_be_stored(tid) and \
                    self.req.has_task(tid):
                task = self.req.get_task(tid)
                self.backend.queue_set_task(task)
        while not self.please_quit or bypass_please_quit:
            try:
                tid = self.to_remove.pop()
            except IndexError:
                break
            self.backend.queue_remove_task(tid)
        # we release the weak lock
        self.to_set_timer = None

    def queue_remove_task(self, tid, path=None):
        """
        Queues task to be removed.

        @param sender: not used, any value will do
        @param tid: The Task ID of the task to be removed
        """
        if tid not in self.to_remove:
            self.to_remove.appendleft(tid)
            self.__try_launch_setting_thread()

    def __try_launch_setting_thread(self):
        """
        Helper function to launch the setting thread, if it's not running
        """
        if self.to_set_timer is None and not self.please_quit:
            self.to_set_timer = threading.Timer(self.timer_timestep,
                                                self.launch_setting_thread)
            self.to_set_timer.setDaemon(True)
            self.to_set_timer.start()

    def initialize(self, connect_signals=True):
        """
        Initializes the backend and starts looking for signals.

        @param connect_signals: if True, it starts listening for signals
        """
        self.backend.initialize()
        if connect_signals:
            self._connect_signals()

    def _connect_signals(self):
        """
        Helper function to connect signals
        """
        if not self.add_task_handle:
            self.add_task_handle = self.tasktree.register_cllbck(
                'node-added', self.queue_set_task)
        if not self.set_task_handle:
            self.set_task_handle = self.tasktree.register_cllbck(
                'node-modified', self.queue_set_task)
        if not self.remove_task_handle:
            self.remove_task_handle = self.tasktree.register_cllbck(
                'node-deleted', self.queue_remove_task)

    def _disconnect_signals(self):
        """
        Helper function to disconnect signals
        """
        if self.add_task_handle:
            self.tasktree.deregister_cllbck('node-added',
                                            self.set_task_handle)
            self.add_task_handle = None
        if self.set_task_handle:
            self.tasktree.deregister_cllbck('node-modified',
                                            self.set_task_handle)
            self.set_task_handle = None
        if self.remove_task_handle:
            self.tasktree.deregister_cllbck('node-deleted',
                                            self.remove_task_handle)
            self.remove_task_handle = None

    def sync(self):
        """
        Forces the TaskSource to sync all the pending tasks
        """
        try:
            self.to_set_timer.cancel()
        except Exception:
            pass
        try:
            self.to_set_timer.join(3)
        except Exception:
            pass
        try:
            self.start_get_tasks_thread.join(3)
        except Exception:
            pass
        self.launch_setting_thread(bypass_please_quit=True)

    def quit(self, disable=False):
        """
        Quits the backend and disconnect the signals

        @param disable: if True, the backend is disabled.
        """
        self._disconnect_signals()
        self.please_quit = True
        self.sync()
        self.backend.quit(disable)

    def __getattr__(self, attr):
        """
        Delegates all the functions not defined here to the real backend
        (standard python function)

        @param attr: attribute to get
        """
        if attr in self.__dict__:
            return self.__dict__[attr]
        else:
            return getattr(self.backend, attr)
