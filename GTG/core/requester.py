# -----------------------------------------------------------------------------
# Getting Things GNOME! - a personal organizer for the GNOME desktop
# Copyright (c) 2008-2013 - Lionel Dricot & Bertrand Rousseau
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

"""
A nice general purpose interface for the datastore and tagstore
"""
import logging
from gi.repository import GObject

from GTG.core.tag import Tag, SEARCH_TAG_PREFIX

log = logging.getLogger(__name__)


class Requester(GObject.GObject):
    """ A view on a GTG datastore.

    L{Requester} is a stateless object that simply provides a nice API for
    user interfaces to use for datastore operations.

    Multiple L{Requester}s can exist on the same datastore, so they should
    never have state of their own.
    """
    __gsignals__ = {'status-changed': (GObject.SignalFlags.RUN_FIRST, None, (str, str, str,))}

    def __init__(self, datastore, global_conf):
        """Construct a L{Requester}."""
        super().__init__()
        self.ds = datastore
        self._config = global_conf
        self.__basetree = self.ds.get_tasks_tree()

    # Tasks Tree ######################
    # By default, we return the task tree of the main window
    def get_tasks_tree(self, name='active', refresh=True):
        return self.__basetree.get_viewtree(name=name, refresh=refresh)

    def get_main_view(self):
        return self.__basetree.get_main_view()

    def get_basetree(self):
        return self.__basetree

    # Tasks ##########################
    def has_task(self, tid):
        """Does the task 'tid' exist?"""
        return self.ds.has_task(tid)

    def get_task(self, tid):
        """Get the task with the given C{tid}.

        If no such task exists, create it and force the tid to be C{tid}.

        @param tid: The task id.
        @return: A task.
        """
        task = self.ds.get_task(tid)
        return task

    # FIXME unused parameter newtask (maybe for compatibility?)
    def new_task(self, tags=None, newtask=True):
        """Create a new task.

        Note: this modifies the datastore.

        @param pid: The project where the new task will be created.
        @param tags: The tags for the new task. If not provided, then the
            task will have no tags. Tags must be an iterator type containing
            the tags tids
        @param newtask: C{True} if this is creating a new task that never
            existed, C{False} if importing an existing task from a backend.
        @return: A task from the data store
        """
        task = self.ds.new_task()
        if tags:
            for t in tags:
                assert(not isinstance(t, Tag))
                task.tag_added(t)
        return task

    def delete_task(self, tid, recursive=True):
        """Delete the task 'tid' and, by default, delete recursively
        all the childrens.

        Note: this modifies the datastore.

        @param tid: The id of the task to be deleted.
        """
        # send the signal before actually deleting the task !
        log.debug("deleting task %s", tid)
        return self.__basetree.del_node(tid, recursive=recursive)

    # Tags ##########################
    def get_tag_tree(self):
        return self.ds.get_tagstore().get_viewtree(name='activetags')

    def new_tag(self, tagname):
        """Create a new tag called 'tagname'.

        Note: this modifies the datastore.

        @param tagname: The name of the new tag.
        @return: The newly-created tag.
        """
        return self.ds.new_tag(tagname)

    def new_search_tag(self, query):
        """
        Create a new search tag from search query

        Note: this modifies the datastore.

        @param query: Query will be parsed using search parser
        @return:      tag_id
        """
        # ! at the beginning is reserved keyword for liblarch
        if query.startswith('!'):
            label = '_' + query
        else:
            label = query

        # find possible name collisions
        name, number = label, 1
        already_search = False
        while True:
            tag = self.get_tag(SEARCH_TAG_PREFIX + name)
            if tag is None:
                break

            if tag.is_search_tag() and tag.get_attribute("query") == query:
                already_search = True
                break

            # this name is used, adding number
            number += 1
            name = label + ' ' + str(number)

        if not already_search:
            tag = self.ds.new_search_tag(name, query)

        return SEARCH_TAG_PREFIX + name

    def remove_tag(self, name):
        """ calls datastore to remove a given tag """
        self.ds.remove_tag(name)

    def rename_tag(self, oldname, newname):
        self.ds.rename_tag(oldname, newname)

    def get_tag(self, tagname):
        return self.ds.get_tag(tagname)

    def get_used_tags(self):
        """Return tags currently used by a task.

        @return: A list of tag names used by a task.
        """
        tagstore = self.ds.get_tagstore()
        view = tagstore.get_viewtree(name='tag_completion', refresh=False)
        tags = view.get_all_nodes()
        tags.sort(key=str.lower)
        return tags

    def get_all_tags(self):
        """
        Gets all tags from all tasks
        """
        return self.ds.get_tagstore().get_main_view().get_all_nodes()

    def delete_tag(self, tagname):
        my_tag = self.get_tag(tagname)
        for task_id in my_tag.get_related_tasks():
            my_task = self.get_task(task_id)
            my_task.remove_tag(tagname)
            my_task.sync()

    # Backends #######################
    def get_all_backends(self, disabled=False):
        return self.ds.get_all_backends(disabled)

    def register_backend(self, dic):
        return self.ds.register_backend(dic)

    def flush_all_tasks(self, backend_id):
        return self.ds.flush_all_tasks(backend_id)

    def get_backend(self, backend_id):
        return self.ds.get_backend(backend_id)

    def set_backend_enabled(self, backend_id, state):
        return self.ds.set_backend_enabled(backend_id, state)

    def remove_backend(self, backend_id):
        return self.ds.remove_backend(backend_id)

    def backend_change_attached_tags(self, backend_id, tags):
        return self.ds.backend_change_attached_tags(backend_id, tags)

    def save_datastore(self, quit=False):
        return self.ds.save(quit)

    # Config ############################
    def get_config(self, system):
        """ Returns configuration object for subsytem, e.g. browser """
        return self._config.get_subconfig(system)

    def get_task_config(self, task_id):
        """ Returns configuration object for task """
        return self._config.get_task_config(task_id)
