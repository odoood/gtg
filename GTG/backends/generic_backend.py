# -----------------------------------------------------------------------------
# Getting Things GNOME! - a personal organizer for the GNOME desktop
# Copyright (c) 2023 - odoood
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
This file contains the most generic representation of a backend,
the GenericBackend class
"""

from collections import deque
from functools import reduce
from datetime import datetime
import os
import shutil
import threading
import logging

from GTG.backends.backend_signals import BackendSignals
from GTG.core.tag import ALLTASKS_TAG
from GTG.core.interruptible import _cancellation_point
from GTG.core.keyring import Keyring
from GTG.core.dirs import DATA_DIR
from GTG.core import xml
from GTG.core import firstrun_tasks
from lxml import etree as et
from gettext import gettext as _

log = logging.getLogger(__name__)

# Total amount of backups
BACKUPS = 7

class GenericBackend():
    """
    Localfile backend, which stores your tasks in a XML file in the standard
    XDG_DATA_DIR/gtg folder (the path is configurable).
    An instance of this class is used as the default backend for GTG.
    This backend loads all the tasks stored in the localfile after it's enabled
    and from that point on just writes the changes to the file: it does not
    listen for eventual file changes
    """

    ###########################################################################
    # BACKEND INTERFACE #######################################################
    ###########################################################################
    # General description of the backend: these parameters are used
    # to show a description of the backend to the user when s/he is
    # considering adding it.
    # _general_description has this format:
    # _general_description = {
    #    GenericBackend.BACKEND_NAME:       "backend_unique_identifier", \
    #    GenericBackend.BACKEND_HUMAN_NAME: _("Human friendly name"), \
    #    GenericBackend.BACKEND_AUTHORS:    ["First author", \
    #                                        "Chuck Norris"], \
    #    GenericBackend.BACKEND_TYPE:       GenericBackend.TYPE_READWRITE, \
    #    GenericBackend.BACKEND_DESCRIPTION: \
    #        _("Short description of the backend"),\
    #    }
    # The complete list of constants and their meaning is given below.

    # BACKEND TYPE DESCRIPTION
    # Each backend must have a "_general_description" attribute, which
    # is a dictionary that holds the values for the following keys.

    # the backend gtg internal name
    # (doesn't change in translations, *must be unique*)
    BACKEND_NAME = "name"
    BACKEND_ICON = "icon"
    BACKEND_HUMAN_NAME = "human-friendly-name"  # The name shown to the user
    BACKEND_DESCRIPTION = "description"  # A short description of the backend
    BACKEND_AUTHORS = "authors"  # a list of strings
    BACKEND_TYPE = "type"
    # BACKEND_TYPE is one of:
    TYPE_READWRITE = "readwrite"
    TYPE_READONLY = "readonly"
    TYPE_IMPORT = "import"
    TYPE_EXPORT = "export"

    # "static_parameters" is a dictionary of dictionaries, each of which
    # are a description of a parameter needed to configure the backend and
    # is identified in the outer dictionary by a key which is the name of the
    # parameter.
    # Each dictionary contains the keys:
    PARAM_DEFAULT_VALUE = "default_value"  # its default value
    PARAM_TYPE = "type"
    # PARAM_TYPE is one of the following (changing this changes the way
    # the user can configure the parameter)
    # the real password is stored in the GNOME keyring
    # This is just a key to find it there
    TYPE_PASSWORD = "password"
    TYPE_STRING = "string"  # generic string, nothing fancy is done
    TYPE_INT = "int"  # edit box can contain only integers
    TYPE_BOOL = "bool"  # checkbox is shown
    # list of strings. the "," character is prohibited in strings
    TYPE_LIST_OF_STRINGS = "liststring"

    # These parameters are common to all backends and necessary.
    # They will be added automatically to your _static_parameters list
    # NOTE: for now I'm disabling changing the default backend. Once it's all
    #      set up, we will see about that (invernizzi)
    KEY_DEFAULT_BACKEND = "Default"
    KEY_ENABLED = "enabled"
    KEY_HUMAN_NAME = BACKEND_HUMAN_NAME
    KEY_ATTACHED_TAGS = "attached-tags"
    KEY_USER = "user"
    KEY_PID = "pid"

    _static_parameters_obligatory = {
        KEY_DEFAULT_BACKEND: {
            PARAM_TYPE: TYPE_BOOL,
            PARAM_DEFAULT_VALUE: False,
        },
        KEY_HUMAN_NAME: {
            PARAM_TYPE: TYPE_STRING,
            PARAM_DEFAULT_VALUE: "",
        },
        KEY_USER: {
            PARAM_TYPE: TYPE_STRING,
            PARAM_DEFAULT_VALUE: "",
        },
        KEY_PID: {
            PARAM_TYPE: TYPE_STRING,
            PARAM_DEFAULT_VALUE: "",
        },
        KEY_ENABLED: {
            PARAM_TYPE: TYPE_BOOL,
            PARAM_DEFAULT_VALUE: False,
        }}

    _static_parameters_obligatory_for_rw = {
        KEY_ATTACHED_TAGS: {
            PARAM_TYPE: TYPE_LIST_OF_STRINGS,
            PARAM_DEFAULT_VALUE: [ALLTASKS_TAG],
        }}

    # Handy dictionary used in type conversion (from string to type)
    _type_converter = {TYPE_STRING: str,
                       TYPE_INT: int,
                       }

    # General description of the backend: these are used to show a description
    # of the backend to the user when s/he is considering adding it.
    # BACKEND_NAME is the name of the backend used internally (it must be
    # unique).
    # Please note that BACKEND_NAME and BACKEND_ICON_NAME should *not* be
    # translated.
    _general_description = {
        BACKEND_NAME: 'backend_localfile',
        BACKEND_ICON: 'folder',
        BACKEND_HUMAN_NAME: _('Local File'),
        BACKEND_AUTHORS: ['Lionel Dricot',
                                         'Luca Invernizzi'],
        BACKEND_TYPE: TYPE_READWRITE,
        BACKEND_DESCRIPTION:
        _(('Your tasks are saved in a text file (XML format). '
           ' This is the most basic and the default way '
           'for GTG to save your tasks.')),
    }

    # These are the parameters to configure a new backend of this type. A
    # parameter has a name, a type and a default value.
    # _static_parameters has this format:
    # _static_parameters = { \
    #    "param1_name": { \
    #        GenericBackend.PARAM_TYPE: GenericBackend.TYPE_STRING,
    #        GenericBackend.PARAM_DEFAULT_VALUE: "my default value",
    #    },
    #    "param2_name": {
    #        GenericBackend.PARAM_TYPE: GenericBackend.TYPE_INT,
    #        GenericBackend.PARAM_DEFAULT_VALUE: 42,
    #        }}
    #
    # Here, we define a parameter "path", which is a string, and has a default
    # value as a random file in the default path
    _static_parameters = {
        "path": {
            PARAM_TYPE: TYPE_STRING,
            PARAM_DEFAULT_VALUE:
            'gtg_data.xml'}}

    def initialize(self):
        """
        Called each time it is enabled (including on backend creation).
        Please note that a class instance for each disabled backend *is*
        created, but it's not initialized.
        Optional.
        NOTE: make sure to call super().initialize()
        """
        self._parameters[self.KEY_ENABLED] = True
        self._is_initialized = True
        # we signal that the backend has been enabled
        self._signal_manager.backend_state_changed(self.get_id())

        filepath = self.get_path()

        if not os.path.isfile(filepath):
            self.do_first_run_versioning()

        self.data_tree = xml.open_file(filepath, 'gtgData')
        self.task_tree = self.data_tree.find('tasklist')
        self.tag_tree = self.data_tree.find('taglist')
        self.search_tree = self.data_tree.find('searchlist')

        self.datastore.load_tag_tree(self.tag_tree)
        self.datastore.load_search_tree(self.search_tree)

        # Make safety daily backup after loading
        self._save_file(self.get_path(), self.data_tree)
        self._write_backups(self.get_path())


    def quit(self, disable=False):
        """
        Called when GTG quits or the user wants to disable the backend.

        @param disable: If disable is True, the backend won't
                        be automatically loaded when GTG starts
        """
        if self._parameters[self.KEY_ENABLED]:
            self._is_initialized = False
            if disable:
                self._parameters[self.KEY_ENABLED] = False
                # we signal that we have been disabled
                self._signal_manager.backend_state_changed(self.get_id())
                self._signal_manager.backend_sync_ended(self.get_id())
            threading.Thread(target=self.sync).run()

    def _create_dirs(self, filepath: str) -> None:
        """Create directory tree for filepath."""

        base_dir = os.path.dirname(filepath)
        try:
            os.makedirs(base_dir, exist_ok=True)
        except IOError as error:
            log.error("Error while creating directories: %r", error)

    def _get_backup_name(self, filepath: str, i: int) -> str:
        """Get name of backups which are backup/ directory."""

        dirname, filename = os.path.split(filepath)
        backup_file = f"{filename}.bak.{i}" if i else filename

        return os.path.join(dirname, 'backup', backup_file)

    def _write_backups(self, filepath: str) -> None:
        """Make backups for the file at filepath."""

        current_back = BACKUPS
        backup_name = self._get_backup_name(filepath, None)
        backup_dir = os.path.dirname(backup_name)

        # Make sure backup dir exists
        try:
            os.makedirs(backup_dir, exist_ok=True)

        except IOError:
            log.error('Backup dir %r cannot be created!', backup_dir)
            return

        # Cycle backups
        while current_back > 0:
            older = f"{backup_name}.bak.{current_back}"
            newer = f"{backup_name}.bak.{current_back - 1}"

            if os.path.exists(newer):
                shutil.move(newer, older)

            current_back -= 1

        # bak.0 is always a fresh copy of the closed file
        # so that it's not touched in case of not opening next time
        bak_0 = f"{backup_name}.bak.0"
        shutil.copy(filepath, bak_0)

        # Add daily backup
        today = datetime.today().strftime('%Y-%m-%d')
        daily_backup = f'{backup_name}.{today}.bak'

        if not os.path.exists(daily_backup):
            shutil.copy(filepath, daily_backup)

    def _save_file(self, filepath: str, root: et.ElementTree) -> None:
        """Save an XML file."""

        temp_file = filepath + '__'

        if os.path.exists(filepath):
            os.rename(filepath, temp_file)

        try:
            with open(filepath, 'wb') as stream:
                root.write(stream, xml_declaration=True, pretty_print=True,
                           encoding='UTF-8')

            if os.path.exists(temp_file):
                os.remove(temp_file)

        except (IOError, FileNotFoundError):
            log.error('Could not write XML file at %r', filepath)

###############################################################################
# You don't need to reimplement the functions below this line #################
###############################################################################

    def __init__(self, parameters):
        """
        Instantiates a new backend. Please note that this is called also
        for disabled backends. Those are not initialized, so you might
        want to check out the initialize() function.
        """
        if self.KEY_DEFAULT_BACKEND not in parameters:
            # if it's not specified, then this is the default backend
            # (for retro-compatibility with the GTG 0.2 series)
            parameters[self.KEY_DEFAULT_BACKEND] = True

        # default backends should get all the tasks
        no_attached_tags = self.KEY_ATTACHED_TAGS not in parameters and \
            self._general_description[self.BACKEND_TYPE] == self.TYPE_READWRITE

        if parameters[self.KEY_DEFAULT_BACKEND] or no_attached_tags:
            parameters[self.KEY_ATTACHED_TAGS] = [ALLTASKS_TAG]

        self._parameters = parameters
        self._signal_manager = BackendSignals()
        self._is_initialized = False
        # if debugging mode is enabled, tasks should be saved as soon as
        # they're marked as modified. If in normal mode, we prefer speed over
        # easier debugging.
        if log.isEnabledFor(logging.DEBUG):
            self.timer_timestep = 5
        else:
            self.timer_timestep = 1
        self.to_set_timer = None
        self.please_quit = False
        self.cancellation_point = lambda: _cancellation_point(
            lambda: self.please_quit)
        self.to_set = deque()
        self.to_remove = deque()

        if self.KEY_DEFAULT_BACKEND not in parameters:
            parameters[self.KEY_DEFAULT_BACKEND] = True

    @classmethod
    def get_static_parameters(cls):
        """
        Returns a dictionary of parameters necessary to create a backend.
        """
        temp_dic = cls._static_parameters_obligatory.copy()
        if cls._general_description[cls.BACKEND_TYPE] == \
                cls.TYPE_READWRITE:
            for key, value in \
                    cls._static_parameters_obligatory_for_rw.items():
                temp_dic[key] = value
        for key, value in cls._static_parameters.items():
            temp_dic[key] = value
        return temp_dic

    def get_parameters(self):
        """
        Returns a dictionary of the current parameters.
        """
        return self._parameters

    @classmethod
    def get_name(cls):
        """
        Returns the name of the backend as it should be displayed in the UI
        """
        return cls._general_description[cls.BACKEND_NAME]

    @classmethod
    def cast_param_type_from_string(cls, param_value, param_type):
        """
        Parameters are saved in a text format, so we have to cast them to the
        appropriate type on loading. This function does exactly that.

        @param param_value: the actual value of the parameter, in a string
                            format
        @param param_type: the wanted type
        @returns something: the casted param_value
        """
        if param_type in cls._type_converter:
            return cls._type_converter[param_type](param_value)
        elif param_type == cls.TYPE_BOOL:
            if param_value == "True":
                return True
            elif param_value == "False":
                return False
            else:
                raise Exception(f"Unrecognized bool value '{param_type}'")
        elif param_type == cls.TYPE_PASSWORD:
            if param_value == -1:
                return None
            return Keyring().get_password(param_value)
        elif param_type == cls.TYPE_LIST_OF_STRINGS:
            the_list = param_value.split(",")
            if not isinstance(the_list, list):
                the_list = [the_list]
            return the_list
        else:
            raise NotImplemented(f"I don't know what type is '{param_type}'")

    def cast_param_type_to_string(self, param_type, param_value):
        """
        Inverse of cast_param_type_from_string

        @param param_value: the actual value of the parameter
        @param param_type: the type of the parameter (password...)
        @returns something: param_value casted to string
        """
        if param_type == GenericBackend.TYPE_PASSWORD:
            if param_value is None:
                return str(-1)
            else:
                return str(Keyring().set_password(
                    "GTG stored password -" + self.get_id(), param_value))
        elif param_type == GenericBackend.TYPE_LIST_OF_STRINGS:
            if param_value == []:
                return ""
            return reduce(lambda a, b: a + "," + b, param_value)
        else:
            return str(param_value)

    def get_id(self):
        """
        returns the backends id, used in the datastore for indexing backends

        @returns string: the backend id
        """
        return self.get_name() + "@" + self._parameters["pid"]

    def get_parameter_type(self, param_name):
        """
        Given the name of a parameter, returns its type. If the parameter is
         one of the default ones, it does not have a type: in that case, it
        returns None

        @param param_name: the name of the parameter
        @returns string: the type, or None
        """
        try:
            return self.get_static_parameters()[param_name][self.PARAM_TYPE]
        except Exception:
            return None

    def register_datastore(self, datastore):
        """
        Setter function to inform the backend about the datastore that's
        loading it.

        @param datastore: a Datastore
        """
        self.datastore = datastore

###############################################################################
# THREADING ###################################################################
###############################################################################
    def __try_launch_setting_thread(self):
        """
        Helper function to launch the setting thread, if it's not running.
        """
        if self.to_set_timer is None:
            self.to_set_timer = threading.Timer(self.timer_timestep,
                                                self.launch_setting_thread)
            self.to_set_timer.start()

    def launch_setting_thread(self, bypass_quit_request=False):
        """
        This function is launched as a separate thread. Its job is to perform
        the changes that have been issued from GTG core.
        In particular, for each task in the self.to_set queue, a task
        has to be modified or to be created (if the tid is new), and for
        each task in the self.to_remove queue, a task has to be deleted

        @param bypass_quit_request: if True, the thread should not be stopped
                                    even if asked by self.please_quit = True.
                                    It's used when the backend quits, to finish
                                    syncing all pending tasks
        """
        while not self.please_quit or bypass_quit_request:
            try:
                task = self.to_set.pop()
            except IndexError:
                break
            tid = task.get_id()
            if tid not in self.to_remove:
                self.set_task(task)

        while not self.please_quit or bypass_quit_request:
            try:
                tid = self.to_remove.pop()
            except IndexError:
                break
            self.remove_task(tid)
        # we release the weak lock
        self.to_set_timer = None

    def queue_set_task(self, task):
        """ Save the task in the backend. In particular, it just enqueues the
        task in the self.to_set queue. A thread will shortly run to apply the
        requested changes.

        @param task: the task that should be saved
        """
        tid = task.get_id()
        if task not in self.to_set and tid not in self.to_remove:
            self.to_set.appendleft(task)
            self.__try_launch_setting_thread()

    def queue_remove_task(self, tid):
        """
        Queues task to be removed. In particular, it just enqueues the
        task in the self.to_remove queue. A thread will shortly run to apply
        the requested changes.

        @param tid: The Task ID of the task to be removed
        """
        if tid not in self.to_remove:
            self.to_remove.appendleft(tid)
            self.__try_launch_setting_thread()
            return None

    def sync(self):
        """
        Helper method. Forces the backend to perform all the pending changes.
        It is usually called upon quitting the backend.
        """
        if self.to_set_timer is not None:
            self.please_quit = True
            try:
                self.to_set_timer.cancel()
            except Exception:
                pass
            try:
                self.to_set_timer.join()
            except Exception:
                pass
        self.launch_setting_thread(bypass_quit_request=True)

    #---------------------------------------------------------------------------
    # XXX: COPIED FROM BACKEND LOCAL
    #---------------------------------------------------------------------------

    def get_path(self) -> str:
        """Return the current path to XML

        Path can be relative to projects.xml
        """
        path = self._parameters['path']

        # This is local path, convert it to absolute path
        if os.sep not in path:
            path = os.path.join(DATA_DIR, path)

        return os.path.abspath(path)


    def this_is_the_first_run(self, _) -> None:
        """ Called upon the very first GTG startup.

        This function is needed only in this backend, because it can be used
        as default one. The xml parameter is an object containing GTG default
        tasks. It will be saved to a file, and the backend will be set as
        default.

        @param xml: an xml object containing the default tasks.
        """

        filepath = self.get_path()
        self.do_first_run_versioning()


        self._parameters[self.KEY_DEFAULT_BACKEND] = True

        # Load the newly created file
        self.data_tree = xml.open_file(self.get_path(), 'gtgData')
        self.task_tree = self.data_tree.find('tasklist')
        self.tag_tree = self.data_tree.find('taglist')


    def do_first_run_versioning(self) -> None:
        """If there is an old file around needing versioning, convert it, then rename the old file."""
        root = firstrun_tasks.generate()
        self._create_dirs(self.get_path())
        self._save_file(self.get_path(), root)


    def start_get_tasks(self) -> None:
        """ This function starts submitting the tasks from the XML file into
        GTG core. It's run as a separate thread.

        @return: start_get_tasks() might not return or finish
        """

        for element in self.task_tree.iter('task'):
            tid = element.get('id')
            task = self.datastore.task_factory(tid)

            if task:
                task = xml.task_from_element(task, element)
                self.datastore.push_task(task)


    def set_task(self, task) -> None:
        """
        This function is called from GTG core whenever a task should be
        saved, either because it's a new one or it has been modified.
        This function will look into the loaded XML object if the task is
        present, and if it's not, it will create it. Then, it will save the
        task data in the XML object.

        @param task: the task object to save
        """

        tid = task.get_id()
        element = xml.task_to_element(task)
        existing = self.task_tree.findall(f"task[@id='{tid}']")

        if existing and element != existing[0]:
            existing[0].getparent().replace(existing[0], element)

        else:
            self.task_tree.append(element)

        # Write the xml
        self._save_file(self.get_path(), self.data_tree)

    def remove_task(self, tid: str) -> None:
        """ This function is called from GTG core whenever a task must be
        removed from the backend. Note that the task could be not present here.

        @param tid: the id of the task to delete
        """

        element = self.task_tree.findall(f'task[@id="{tid}"]')

        if element:
            element[0].getparent().remove(element[0])
            self._save_file(self.get_path(), self.data_tree)

    def save_tags(self, tagnames, tagstore) -> None:
        """Save changes to tags and saved searches."""

        already_saved = []
        self.search_tree.clear()
        self.tag_tree.clear()

        for tagname in tagnames:
            if tagname in already_saved:
                continue

            tag = tagstore.get_node(tagname)

            attributes = tag.get_all_attributes(butname=True, withparent=True)
            if "special" in attributes:
                continue

            if tag.is_search_tag():
                root = self.search_tree
                tag_type = 'savedSearch'
            else:
                root = self.tag_tree
                tag_type = 'tag'

            tid = str(tag.tid)
            element = root.findall(f'{tag_type}[@id="{tid}"]')

            if len(element) == 0:
                element = et.SubElement(self.task_tree, tag_type)
                root.append(element)
            else:
                element = element[0]

            # Don't save the @ in the name
            element.set('id', tid)
            element.set('name', tag.get_friendly_name())

            # Remove these and don't re-add them if not needed
            element.attrib.pop('icon', None)
            element.attrib.pop('color', None)
            element.attrib.pop('parent', None)

            for attr in attributes:
                # skip labels for search tags
                if tag.is_search_tag() and attr == 'label':
                    continue

                value = tag.get_attribute(attr)

                if value:
                    if attr == 'color':
                        value = value[1:]
                    element.set(attr, value)

            already_saved.append(tagname)

        self._save_file(self.get_path(), self.data_tree)
