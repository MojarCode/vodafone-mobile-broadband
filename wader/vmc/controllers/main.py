# -*- coding: utf-8 -*-
# Copyright (C) 2008-2009  Warp Networks, S.L.
# Author:  Jaime Soriano, Isaac Clerencia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
"""
Main controller for the application
"""

import os
import gtk
from twisted.internet.utils import getProcessOutput

#from gtkmvc import Controller
from wader.vmc.controllers.base import WidgetController, TV_DICT, TV_DICT_REV
from wader.vmc.controllers.contacts import (AddContactController,
                                          SearchContactController)
from wader.vmc.views.contacts import AddContactView, SearchContactView

import wader.common.consts as consts
from wader.common.signals import SIG_SMS
from wader.common.keyring import KeyringInvalidPassword
from wader.vmc.logger import logger
from wader.vmc.dialogs import (show_profile_window,
                               show_warning_dialog, ActivityProgressBar,
                               show_about_dialog, show_error_dialog,
                               ask_pin_dialog, ask_puk_dialog,
                               ask_puk2_dialog, ask_password_dialog)
#from wader.vmc.keyring_dialogs import NewKeyringDialog, KeyringPasswordDialog
from wader.vmc.utils import bytes_repr, get_error_msg, UNIT_MB, units_to_bits
from wader.vmc.translate import _
from wader.vmc.notify import new_notification
from wader.vmc.consts import GTK_LOCK, GLADE_DIR, IMAGES_DIR

from wader.vmc.phonebook import (get_phonebook,
                                all_same_type, all_contacts_writable)

from wader.vmc.models.diagnostics import DiagnosticsModel
from wader.vmc.controllers.diagnostics import DiagnosticsController
from wader.vmc.views.diagnostics import DiagnosticsView

def get_fake_toggle_button():
    """Returns a toggled L{gtk.ToggleToolButton}"""
    button = gtk.ToggleToolButton()
    button.set_active(True)
    return button

class MainController(WidgetController):
    """
    I am the controller for the main window
    """
    def __init__(self, model):
        super(MainController, self).__init__(model)
        model.ctrl = self
        self.cid = None

        self.signal_matches = []

        # activity progress bar
        self.apb = None
        self.icon = None

    def register_view(self, view):
        super(MainController, self).register_view(view)
        self._setup_icon()
        self.view.set_initialising(True)
        self.connect_to_signals()
        self.start()

    def _setup_icon(self):
        filename = os.path.join(GLADE_DIR, 'VF_logo_medium.png')
        self.icon = gtk.status_icon_new_from_file(filename)

    def start(self):
        self.view.set_disconnected()

        # we're on SMS mode
        self.on_sms_button_toggled(get_fake_toggle_button())

        #self.usage_updater = TimerService(5, self.update_usage_view)
        #self.usage_updater.startService()

    def connect_to_signals(self):
# VMC        self._setup_menubar_hacks()

        self.view['main_window'].connect('delete_event', self.close_application)
# VMC        self.view.get_top_widget().connect("delete_event", self._quit_or_minimize)

        self.cid = self.view['connect_button'].connect('toggled',
                                            self.on_connect_button_toggled)

#        self.icon.connect('activate', self.on_icon_activated)

#        if config.getboolean('preferences', 'show_icon'):
        if True:
            self.icon.connect('popup-menu', self.on_icon_popup_menu)

        for treeview_name in list(set(TV_DICT.values())):
            treeview = self.view[treeview_name]
            treeview.connect('key_press_event', self.__on_treeview_key_press)
            if treeview.name != 'contacts_treeview':
                treeview.connect('row-activated', self._row_activated_tv)

    def close_application(self, *args):
        if self.model.dial_path:
            show_warning_dialog(_("Can not close application"),
                               _("Can not close while a connection is active"))
            window = self.view.get_top_widget()
            try:
                window.emit_stop_by_name('delete_event')
            except IOError:
                pass

            return True
        else:
            self.view.start_throbber()
            self.model.quit(self._close_application_cb)

    def ask_for_new_profile(self):
        model = self.model.preferences_model
        if self.model.device:
            self.model.get_imsi(lambda imsi:
                show_profile_window(model, imsi=imsi))
        else:
            show_profile_window(model)

    def ask_for_pin(self):
        pin = ask_pin_dialog(self.view)
        if pin:
            self.model.send_pin(pin, self.model.enable_device)

    def _close_application_cb(self, *args):
        try:
            os.unlink(GTK_LOCK)
        except OSError:
            pass

        self.view.stop_throbber()
        gtk.main_quit()

    def _setup_connection_signals(self):
        self.model.bus.add_signal_receiver(
                                self._on_disconnect_cb,
                                "Disconnected",
                                dbus_interface=consts.WADER_DIALUP_INTFACE)
    # properties
    def property_rssi_value_change(self, model, old, new):
        self.view.rssi_changed(new)

    def on_net_password_required(self, opath, tag):
        password = ask_password_dialog(self.view)

        if password:
            from wader.vmc.profiles import manager
            profile = manager.get_profile_by_object_path(opath)
            # XXX: do not hardcode NM_PASSWD
            ret = {tag : {consts.NM_PASSWD :password}}
            profile.set_secrets(tag, ret)

    def on_keyring_password_required(self, opath):
        from wader.vmc.profiles import manager
        profile = manager.get_profile_by_object_path(opath)
        password = None

#        if profile.secrets.manager.is_new:
#            dialog = NewKeyringDialog(self.view.get_top_widget())
#            response = dialog.run()
#        else:
#            # profile.secrets.manager.is_open == True
#            dialog = KeyringPasswordDialog(self.view.get_top_widget())
#            response = dialog.run()
#
#        if response == gtk.RESPONSE_OK:
#            password = dialog.password_entry.get_text()
#
#        dialog.destroy()

        if password is not None:
            try:
                profile.secrets.manager.open(password)
            except KeyringInvalidPassword:
                title = _("Invalid password")
                details = _("The supplied password is incorrect")
                show_error_dialog(title, details)
                # call ourselves again
                self.on_keyring_password_required(opath)

    def property_operator_value_change(self, model, old, new):
        if new == _('Unknown Network'):
            logger.error("Unknown operator received, using profile name...")
            profiles_model = self.model.preferences_model.profiles_model
            try:
                profile = profiles_model.get_active_profile()
            except RuntimeError:
                self.view.operator_changed(new)
            else:
                self.view.operator_changed(profile.name)
        else:
            self.view.operator_changed(new)

    def property_tech_value_change(self, model, old, new):
        self.view.tech_changed(new)

    def property_device_value_change(self, model, old, new):
        if self.model.device is not None:
            sm = self.model.device.connect_to_signal("DeviceEnabled",
                                            self.on_device_enabled_cb)
            self.signal_matches.append(sm)
            # connect to SIG_SMS and display SMS
            sm = self.model.device.connect_to_signal(SIG_SMS,
                                                self.on_sms_received_cb)
            self.signal_matches.append(sm)
        else:
            while self.signal_matches:
                sm = self.signal_matches.pop()
                sm.remove()

    def property_profile_value_change(self, model, old, new):
        logger.info("A profile has been set for current model %s" % new)

    def property_status_value_change(self, model, old, new):
        self.view.set_status(new)
        if new == _('Initialising'):
            self.view.set_initialising(True)
        elif new == _('No device'):
            self.view.set_disconnected(device_present=False)
        elif new in [_('Registered'), _('Roaming')]:
            self.view.set_initialising(False)

    def property_sim_error_value_change(self, model, old, details):
        title = _('Unknown error while initting device')
        show_error_dialog(title, details)

    def property_net_error_value_change(self, model, old, new):
        title = _("Error while registering to home network")
        show_error_dialog(title, new)

    def property_pin_required_value_change(self, model, old, new):
        if new:
            self.ask_for_pin()

    def property_puk_required_value_change(self, model, old, new):
        if new:
            pukinfo = ask_puk_dialog(parent=self.view)
            if pukinfo:
                puk, pin = pukinfo
                model.send_puk(puk, pin, model.enable_device)

    def property_puk2_required_value_change(self, model, old, new):
        if new:
            pukinfo = ask_puk2_dialog(parent=self.view)
            if pukinfo:
                puk2, pin = pukinfo
                model.send_puk(puk2, pin, model.enable_device)

    def property_rx_bytes_value_change(self, model, old, new):
        if old != new:
            self.view['rx_bytes_label'].set_text(bytes_repr(new))
            logger.info("Bytes rx: %d", new)

    def property_tx_bytes_value_change(self, model, old, new):
        if old != new:
            self.view['tx_bytes_label'].set_text(bytes_repr(new))
            logger.info("Bytes tx: %d", new)

    def property_total_bytes_value_change(self, model, old, new):
        if old != new and new != '':
            self.view['total_bytes_label'].set_text(bytes_repr(new))
            logger.info("Total bytes: %d", new)

    def property_transfer_limit_exceeded_value_change(self, model, old, new):
        if not old and new:
            show_warning_dialog(_("Transfer limit exceeded"),
                                _("You have exceeded your transfer limit"))

    # callbacks

    def on_sms_menu_item_activate(self, widget):
        self.on_sms_button_toggled(get_fake_toggle_button())

    def on_usage_menu_item_activate(self, widget):
        self.on_usage_button_clicked(get_fake_toggle_button())

    def on_support_menu_item_activate(self, widget):
        self.on_support_button_toggled(get_fake_toggle_button())

    def on_sms_button_toggled(self, widget):
        if widget.get_active():
            self.view['usage_frame'].hide()
            self.view['usage_tool_button'].set_active(False)
            self.view['support_tool_button'].set_active(False)
            self.view['support_notebook'].hide()
            self.view['sms_frame_alignment'].show()
            self.view['sms_tool_button'].set_active(True)

    def on_usage_button_clicked(self, widget):
        if widget.get_active():
            self.view['sms_frame_alignment'].hide()
            self.view['sms_tool_button'].set_active(False)
            self.view['support_notebook'].hide()
            self.view['support_tool_button'].set_active(False)
            self.view['usage_frame'].show()
            self.view['usage_tool_button'].set_active(True)

    def on_support_button_toggled(self, widget):
        if widget.get_active():
            self.view['usage_frame'].hide()
            self.view['usage_tool_button'].set_active(False)
            self.view['sms_frame_alignment'].hide()
            self.view['sms_tool_button'].set_active(False)
            self.view['support_notebook'].show()
            self.view['support_tool_button'].set_active(True)

    def on_internet_button_clicked(self, widget):
        pass
#        if self._check_if_connected():
#            binary = config.get('preferences', 'browser')
#            getProcessOutput(binary, [consts.APP_URL], os.environ)

    def on_mail_button_clicked(self, widget):
        pass
#        if self._check_if_connected():
#            binary = config.get('preferences', 'mail')
#            getProcessOutput(binary, ['REPLACE@ME.COM'], os.environ)

    def on_sms_received_cb(self, index):
        """
        Executed whenever a new SMS is received

        Will read and show the SMS to the user
        """
        def process_sms_eb(error):
            title = _("Error reading SMS %d") % index
            show_error_dialog(title, get_error_msg(error))

         # read the SMS and show it to the user
        self.model.device.Get(index,
                              dbus_interface=consts.SMS_INTFACE,
                              reply_handler=self.show_sms_notification,
                              error_handler=process_sms_eb)

    def show_sms_notification(self, sms):
        """
        Shows a notification when a SMS is received

        It will take care of looking up the number in the phonebook
        to show the name if its a known contact instead of its number
        """
        def find_by_number_cb(contacts):
            if not contacts:
                title = _("SMS received from %s") % sms['number']
            else:
                assert len(contacts) == 1, "More than one match for a number!"
                title = _("SMS received from %s") % contacts[0][1]

            n = new_notification(self.icon, title, sms['text'],
                                 stock=gtk.STOCK_INFO)
            n.show()

        self.model.device.FindByNumber(sms['number'],
                                       dbus_interface=consts.CTS_INTFACE,
                                       reply_handler=find_by_number_cb,
                                       error_handler=logger.error)

    def on_device_enabled_cb(self, udi):
        pass
#        self.view['sms_menuitem'].set_sensitive(True)
#        self.view['preferences_menu_item'].set_sensitive(True)

    def _on_connect_cb(self, dev_path):
        self.view.set_connected()
        self.model.start_stats_tracking()
        if self.apb:
            self.apb.close()
            self.apb = None

        self.model.dial_path = dev_path

    def _on_connect_eb(self, e):
        logger.error(e)
        self.view.set_disconnected()
        if self.apb:
            self.apb.close()
            self.apb = None

        title = _('Failed connection attempt')
        show_error_dialog(title, get_error_msg(e))

    def _on_disconnect_cb(self, *args):
        logger.info("Disconnected")
        self.model.stop_stats_tracking()
        self.view.set_disconnected()

        if self.apb:
            self.apb.close()
            self.apb = None

        if self.model.dial_path:
            # if dial_path is not None, it means that the connection was
            # deactivated externally to wader, either through nm-applet
            # or because pppd died or something.
            dialmanager = self.model.get_dialer_manager()
            dialmanager.DeactivateConnection(self.model.dial_path)
            self.model.dial_path = None

    def _on_disconnect_eb(self, args):
        logger.error(args)
        self.model.stop_stats_tracking()
        if self.apb:
            self.apb.close()
            self.apb = None

    def on_icon_activated(self, icon):
        window = self.view.get_top_widget()
        if window.get_property('visible'):
            window.hide()
        else:
            window.present()

    def get_trayicon_menu(self):

        connect_button = self.view['connect_button']

        def _disconnect_from_inet(widget):
            connect_button.set_active(False)

        def _connect_to_inet(widget):
            connect_button.set_active(True)

        menu = gtk.Menu()

#        if self.model.is_connected():
        if self.model.dial_path:
            item = gtk.ImageMenuItem(_("Disconnect"))
            img = gtk.Image()
            img.set_from_file(os.path.join(IMAGES_DIR, 'stop16x16.png'))
            item.connect("activate", _disconnect_from_inet)
        else:
            item = gtk.ImageMenuItem(_("Connect"))
            img = gtk.Image()
            img.set_from_file(os.path.join(IMAGES_DIR, 'connect-16x16.png'))
            item.connect("activate", _connect_to_inet)

        item.set_image(img)
        if self.model.device is None:
            item.set_sensitive(False)
        item.show()
        menu.append(item)

        separator = gtk.SeparatorMenuItem()
        separator.show()
        menu.append(separator)

        item = gtk.ImageMenuItem(_("Send SMS"))
        img = gtk.Image()
        img.set_from_file(os.path.join(IMAGES_DIR, 'sms16x16.png'))
        item.set_image(img)
        item.connect("activate", self.on_new_sms_activate)
        if self.model.device is None:
            item.set_sensitive(False)
        item.show()
        menu.append(item)

        separator = gtk.SeparatorMenuItem()
        separator.show()
        menu.append(separator)

        item = gtk.ImageMenuItem(_("Quit"))
        img = gtk.image_new_from_stock(gtk.STOCK_QUIT, gtk.ICON_SIZE_MENU)
        item.set_image(img)
        item.connect("activate", self.on_quit_menu_item_activate)
        item.show()
        menu.append(item)

        return menu

    def get_contacts_popup_menu(self, pathinfo, treeview):
        """Returns a popup menu for the contacts treeview"""
        selection = treeview.get_selection()
        model, selected = selection.get_selected_rows()
        iters = [model.get_iter(path) for path in selected]
        contacts = [model.get_value(_iter, 3) for _iter in iters]

        menu = gtk.Menu()

        item = gtk.ImageMenuItem(_("_Send SMS"))
        item.connect("activate", self._send_sms_to_contact, treeview)
        img = gtk.Image()
        img.set_from_file(os.path.join(IMAGES_DIR, 'sms16x16.png'))
        item.set_image(img)
        item.show()
        menu.append(item)

        # Figure out whether we should show delete, edit, or no extra menu items
        if all_contacts_writable(contacts):
            item = gtk.ImageMenuItem(_("_Delete"))
            img = gtk.image_new_from_stock(gtk.STOCK_DELETE, gtk.ICON_SIZE_MENU)
            item.set_image(img)
            item.connect("activate", self.delete_entries, pathinfo, treeview)
            item.show()
            menu.append(item)

        elif all_same_type(contacts):
            editor = contacts[0].external_editor()
            if editor:
                item = gtk.ImageMenuItem(_("Edit"))
                img = gtk.image_new_from_stock(gtk.STOCK_EDIT, gtk.ICON_SIZE_MENU)
                item.set_image(img)

                item.connect("activate", self._edit_external_contacts, editor)

                item.show()
                menu.append(item)

        return menu

    def on_icon_popup_menu(self, icon, button, activate_time):
        menu = self.get_trayicon_menu()
        menu.popup(None, None, None, button, activate_time)

    def on_connect_button_toggled(self, widget):
        dialmanager = self.model.get_dialer_manager()

        if widget.get_active():
            # user wants to connect
            if not self.model.device:
                widget.set_active(False)
                show_warning_dialog(
                    _("No device found"),
                    _("No device has been found. Insert one and try again."))
                return


            profiles_model = self.model.preferences_model.profiles_model
            if not profiles_model.has_active_profile():
                widget.set_active(False)
                show_warning_dialog(
                    _("Profile needed"),
                    _("You need to create a profile for connecting."))
                self.ask_for_new_profile()
                return

            active_profile = profiles_model.get_active_profile()

            dialmanager.ActivateConnection(active_profile.profile_path,
                                           self.model.device_path,
                                           timeout=40,
                                           reply_handler=self._on_connect_cb,
                                           error_handler=self._on_connect_eb)

            self._setup_connection_signals()

            def cancel_cb():
                self.view.set_disconnected()
                self.model.dial_path = None

            self.apb = ActivityProgressBar(_("Connecting"), self)
            self.apb.set_cancel_cb(dialmanager.StopConnection,
                                   self.model.device_path,
                                   reply_handler=cancel_cb,
                                   error_handler=logger.error)

            self.apb.init()
            logger.info("Connecting...")
        else:
            # user wants to disconnect
            if not self.model.dial_path:
                return

            self.apb = ActivityProgressBar(_("Disconnecting"), self,
                                           disable_cancel=True)
            dialmanager.DeactivateConnection(self.model.dial_path,
                                        reply_handler=self._on_disconnect_cb,
                                        error_handler=self._on_disconnect_eb)

            self.apb.init()
            self.model.dial_path = None

    def on_preferences_menu_item_activate(self, widget):
        print "on_preferences_menu_item_activate"
#        from wader.vmc.views.preferences import PreferencesView
#        from wader.vmc.controllers.preferences import PreferencesController
#
#        controller = PreferencesController(self.model.preferences_model,
#                                           lambda: self.model.device)
#        view = PreferencesView(controller)
#
#        profiles_model = self.model.preferences_model.profiles_model
#        if not profiles_model.has_active_profile():
#            show_warning_dialog(
#                _("Profile needed"),
#                _("You need to create a profile to save preferences"))
#            self.ask_for_new_profile()
#            return
#        view.show()

    def on_sms_menuitem_activate(self, widget):
        from wader.vmc.models.sms import SMSContactsModel
        from wader.vmc.controllers.sms import SMSContactsController
        from wader.vmc.views.sms import SMSContactsView

        model = SMSContactsModel(self.model.device)
        ctrl = SMSContactsController(model, self)
        view = SMSContactsView(ctrl, parent_view=self.view)

        view.show()

    def on_log_menuitem_activate(self, widget):
        from wader.vmc.controllers.log import LogController
        from wader.vmc.views.log import LogView
        from wader.vmc.models.log import LogModel

        model = LogModel()
        ctrl = LogController(model)
        view = LogView(ctrl)

        view.show()

    def on_exit_menu_item_activate(self, widget):
        self.close_application()

########################### copied in from application.py ##############################

    def on_new_contact_menu_item_activate(self, widget):
        self.view['main_notebook'].set_current_page(3) # contacts_tv
        ctrl = AddContactController(self.model, self)
        view = AddContactView(ctrl)
        view.set_parent_view(self.view)
        view.show()

    def on_search_contact_menu_item_activate(self, widget):
        self.view['main_notebook'].set_current_page(3) # contacts_tv
        ctrl = SearchContactController(self.model, self)
        view = SearchContactView(ctrl)
        view.set_parent_view(self.view)
        view.run()

    def on_new_sms_activate(self, widget):
        pass
        #ctrl = NewSmsController(Model(), self)
        #view = NewSmsView(ctrl)
        #view.set_parent_view(self.view)
        #view.show()

    def on_quit_menu_item_activate(self, widget):
        #exit_without_conf = config.getboolean('preferences',
        #                                    'exit_without_confirmation')
        exit_without_conf = True
        if exit_without_conf:
            self.close_application()
            return

        #resp, checked = dialogs.open_dialog_question_checkbox_cancel_ok(
        #            self.view,
        #            _("Quit %s") % APP_LONG_NAME,
        #            _("Are you sure you want to exit?"))

        #config.setboolean('preferences', 'exit_without_confirmation', checked)
        #config.write()
#
#        if resp:
#            self.quit_application()

    def on_new_profile_menuitem_activate(self, widget):
        self.ask_for_new_profile()
        # XXX: shouldn't we make it the active one

    def _build_profiles_menu(self):
        def load_profile(widget, profile):
            profiles_model = self.model.preferences_model.profiles_model
            profiles_model.set_default_profile(profile.uuid)

            # refresh menu
            self.on_tools_menu_item_activate(get_fake_toggle_button())

        def edit_profile(widget, profile):
            show_profile_window(self.model.preferences_model,
                                profile=profile)
            # XXX: check out whether editing a profile should make it active
            #      currently it doesn't
            # self.on_tools_menu_item_activate(get_fake_toggle_button())

        def delete_profile(widget, profile):
            profiles_model = self.model.preferences_model.profiles_model
            profiles_model.remove_profile(profile)

            # refresh menu
            self.on_tools_menu_item_activate(get_fake_toggle_button())

        def is_active_profile(profile):
            profiles_model = self.model.preferences_model.profiles_model

            if not profiles_model.has_active_profile():
                return False
            return profile.uuid == profiles_model.get_active_profile().uuid

        profiles = self.model.preferences_model.get_profiles(None)

        menu1 = gtk.Menu()
        for profile in profiles.values():
            item = gtk.ImageMenuItem(profile.name)
            item.connect("activate", load_profile, profile)
            item.show()
            if is_active_profile(profile):
                item.set_sensitive(False)
            menu1.append(item)

        menu2 = gtk.Menu()
        for profile in profiles.values():
            item = gtk.ImageMenuItem(profile.name)
            item.connect("activate", edit_profile, profile)
            item.show()
            menu2.append(item)

        menu3 = gtk.Menu()
        for profile in profiles.values():
            item = gtk.ImageMenuItem(profile.name)
            item.connect("activate", delete_profile, profile)
            item.show()
            if is_active_profile(profile):
                item.set_sensitive(False)
            menu3.append(item)

        return menu1, menu2, menu3

    def on_tools_menu_item_activate(self, widget):
        # build dynamically the menu with the profiles
        parent = self.view['profiles_menu_item']
        menu = gtk.Menu()

        item = gtk.ImageMenuItem(_("_New Profile"))
        img = gtk.image_new_from_stock(gtk.STOCK_NEW, gtk.ICON_SIZE_MENU)
        item.set_image(img)
        item.connect("activate", self.on_new_profile_menuitem_activate)
        menu.append(item)
        item.show()

        load, edit, delete = self._build_profiles_menu()

        item = gtk.MenuItem(_("Load Profile"))
        item.set_submenu(load)
        menu.append(item)
        item.show()

        item = gtk.MenuItem(_("Edit Profile"))
        item.set_submenu(edit)
        menu.append(item)
        item.show()

        item = gtk.MenuItem(_("Delete Profile"))
        item.set_submenu(delete)
        menu.append(item)
        item.show()

        parent.set_submenu(menu)

    def on_diagnostics_item_activate(self, widget):
        model = DiagnosticsModel(self.model.device)
        ctrl = DiagnosticsController(model, self)
        view = DiagnosticsView(ctrl)
        view.set_parent_view(self.view)
        view.show()

    def on_help_topics_menu_item_activate(self, widget):
        print "on_help_topics_menu_item_activate"
        #binary = config.get('preferences', 'browser')
        #index_path = os.path.join(consts.GUIDE_DIR, 'index.html')
        #getProcessOutput(binary, [index_path], os.environ)

    def on_about_menu_item_activate(self, widget):
        about = show_about_dialog()
        about.run()
        about.destroy()

######

    def _empty_treeviews(self, treeviews):
        for treeview_name in treeviews:
            model = self.view[treeview_name].get_model()
            if model:
                model.clear()

    def _fill_contacts(self, ignored=None):
        """Fills the contacts treeview with contacts"""
        phonebook = get_phonebook(device=self.model.device)

        def process_contacts(contacts):
            treeview = self.view['contacts_treeview']
            model = treeview.get_model()
            model.add_contacts(contacts)

            return contacts

#        d = phonebook.get_contacts()
#        d.addCallback(process_contacts)
#        d.addErrback(log.err)
#        return d

        process_contacts(phonebook.get_contacts())

#    def _fill_messages(self, contacts=None):
#        """
#        Fills the messages treeview with SIM & DB SMS
#
#        We're receiving the contacts list produced by _fill_contacts because
#        otherwise, adding dozens of SMS to the treeview would be very
#        inefficient, as we would have to lookup the sender number of every
#        SMS to find out whether is a known contact or not. The solution is
#        to cache the previous result and pass the contacts list to the
#        L{wader.vmc.models.sms.SMSStoreModel}
#        """
#        messages_obj = get_messages_obj(self.model.get_sconn())
#        self.splash.set_text(_('Reading messages...'))
#        def process_sms(sms_list):
#            for sms in sms_list:
#                active_tv = TV_DICT[sms.where]         # get treeview name
#                treeview = self.view[active_tv]        # get treeview object
#                treeview.get_model().add_message(sms, contacts) # append to tv
#
#            self.splash.pulse()
#            return True
#
#        d = messages_obj.get_messages()
#        d.addCallback(process_sms)
#        return d

    def _fill_treeviews(self):
        """
        Fills the treeviews with SMS and contacts
        """
#        d = self._fill_contacts()
#        d.addCallback(self._fill_messages)
#        d.addErrback(log.err)
#        return d
        self._fill_contacts()
#########

    def _update_usage_panel(self, name, offset):
        m = self.model
        w = lambda label : label % name

        values = ['month', 'transferred_gprs', 'transferred_3g',
                  'transferred_total']
        for value_name in values:
            widget = (value_name + '_%s_label') % name
            value = getattr(m, 'get_%s' % value_name)(offset)
            self.view.set_usage_value(widget, value)

        self.view.set_usage_bar_value('%s-gprs' % name,
                                            m.get_transferred_gprs(offset))
        self.view.set_usage_bar_value('%s-3g' % name,
                                            m.get_transferred_3g(offset))

    def _update_usage_session(self):
        set_value = self.view.set_usage_value
        m = self.model
        set_value('transferred_3g_session_label', m.get_session_3g())
        set_value('transferred_gprs_session_label', m.get_session_gprs())
        set_value('transferred_total_session_label', m.get_session_total())

    def usage_notifier(self):
        #limit = int(config.get('preferences', 'traffic_threshold'))
        #notification = config.getboolean('preferences', 'usage_notification')
        limit = 10
        notification = 0
        limit = units_to_bits(limit, UNIT_MB)
        if (notification and limit > 0
                and self.model.get_transferred_total(0) > limit
                and not self.user_limit_notified):
            self.user_limit_notified = True
            message = _("User Limit")
            details = _("You have reached your limit of maximum usage")
            show_warning_dialog(message, details)

            #show_normal_notification(self.tray, message, details, expires=False)
            n = new_notification(self.icon, message, details, stock=gtk.STOCK_INFO)
            n.show()
        elif self.model.get_transferred_total(0) < limit :
            self.user_limit_notified = False

    def update_usage_view(self):
        self.model.clean_usage_cache()
        self._update_usage_panel('current', 0)
        self._update_usage_panel('last', -1)
        self._update_usage_session()
        self.view.update_bars_user_limit()
        self.usage_notifier()

    def on_generic_treeview_row_button_press_event(self, treeview, event):
        if event.button == 3 and event.type == gtk.gdk.BUTTON_PRESS:
            selection = treeview.get_selection()
            model, pathlist = selection.get_selected_rows()
            if pathlist:
                if treeview.name in ['contacts_treeview']:
                    get_contacts = self.get_contacts_popup_menu
                else:
                    get_contacts = self.get_generic_popup_menu
                menu = get_contacts(pathlist, treeview)
                menu.popup(None, None, None, event.button, event.time)
                return True # selection is not lost

    def on_cursor_changed_treeview_event(self, treeview):
        col = treeview.get_cursor()[0]
        model = treeview.get_model()
        text = model[col][1]
        _buffer = self.view['smsbody_textview'].get_buffer()
        _buffer.set_text(text)
        self.view['vbox17'].show()

    def on_main_notebook_switch_page(self, notebook, ptr, pagenum):
        """
        Callback for whenever VMC's main notebook is switched

        Basically takes care of showing and hiding the appropiate menubars
        depending on the page the user is viewing
        """
        if int(pagenum) == 3:
            self.view['contacts_menubar'].show()
            self.view['sms_menubar'].hide()
        else:
            self.view['contacts_menubar'].hide()
            self.view['sms_menubar'].show()

        self.view['vbox17'].hide()

    #----------------------------------------------#
    # MISC FUNCTIONALITY                           #
    #----------------------------------------------#

    def _name_contact_cell_edited(self, widget, path, newname):
        """Handler for the cell-edited signal of the name column"""
        # first check that the edit is necessary
        model = self.view['contacts_treeview'].get_model()
        if newname != model[path][1] and newname != '':
            contact = model[path][3]
            if contact.set_name(unicode(newname, 'utf8')):
                model[path][1] = newname

    def _number_contact_cell_edited(self, widget, path, newnumber):
        """Handler for the cell-edited signal of the number column"""
        model = self.view['contacts_treeview'].get_model()
        number = newnumber.strip()
        # check that the edit is necessary

        def is_valid_number(number):
            import re
            pattern = re.compile('^\+?\d+$')
            return pattern.match(number) and True or False

        if number != model[path][2] and is_valid_number(number):
            contact = model[path][3]
            if contact.set_number(unicode(number, 'utf8')):
                model[path][2] = number

    def _row_activated_tv(self, treeview, path, col):
        # get selected row
        message = self.get_obj_from_selected_row()
        if message:
            ctrl = ForwardSmsController(Model(), self)
            view = ForwardSmsView(ctrl)
            view.set_parent_view(self.view)
            ctrl.set_textbuffer_text(message.get_text())
            ctrl.set_recipient_numbers(message.get_number())
            if treeview.name in 'drafts_treeview':
                # if the SMS is a draft, delete it after sending it
                ctrl.set_processed_sms(message)
            view.show()

    def __on_treeview_key_press(self, widget, event):
        """Handler for key_press_button in treeviews"""
        from gtk.gdk import keyval_name
#        print keyval_name(event.keyval)

        if keyval_name(event.keyval) in 'F5':
            # get current treeview
            num = self.view['main_notebook'].get_current_page() + 1
            treeview_name = TV_DICT[num]
            # now do the refresh
            if treeview_name in 'contacts_treeview':
                self._empty_treeviews(['contacts_treeview'])
                self._fill_contacts()

        if keyval_name(event.keyval) in 'Delete':
            # get current treeview
            num = self.view['main_notebook'].get_current_page() + 1
            treeview_name = TV_DICT[num]
            treeview = self.view[treeview_name]
            # now delete the entries
            self.delete_entries(None, None, treeview)

    def delete_entries(self, menuitem, pathlist, treeview):
        """
        Deletes the entries selected in the treeview

        This entries are deleted in SIM/DB and the treeview
        """
        model, selected = treeview.get_selection().get_selected_rows()
        iters = [model.get_iter(path) for path in selected]

        # if we are in contacts_treeview the gobject.TYPE_PYOBJECT that
        # contains the contact is at position 3, if we are on a sms treeview,
        # then it's at position 4
        if (treeview.name == 'contacts_treeview'): # filter out the read only items
            pos = 3
            objs = []
            _iters = []
            for _iter in iters:
                obj = model.get_value(_iter, pos)
                if obj.is_writable():
                    objs.append(obj)
                    _iters.append(_iter)
            iters = _iters
        else:
            pos = 4
            objs = [model.get_value(_iter, pos) for _iter in iters]

        if not (len(objs) and iters): # maybe we filtered out everything
            return

        if treeview.name == 'contacts_treeview':
            manager = get_phonebook(self.model.device)
        else:
            manager = get_messages_obj(self.model.device)

        manager.delete_objs(objs)

# XXX: when multiple but mixed writable / readonly are in the selection for delete
#      invalidate the selection after delete or the selection is wrong
        _inxt = None
        for _iter in iters:
            _inxt=model.iter_next(_iter)
            model.remove(_iter) # delete from treeview
        if _inxt:
            treeview.get_selection().select_iter(_inxt) # select next item
        else:
            n_rows = len(model)                         # select last item
            if n_rows > 0:
                _inxt = model[n_rows-1].iter
                treeview.get_selection().select_iter(_inxt)

        # If we are in a sms treeview update displayed text
        if treeview.get_name() != 'contacts_treeview':
            _obj = self.get_obj_from_selected_row()
            if _obj:
                self.view['smsbody_textview'].get_buffer().set_text(_obj.get_text())
                self.view['vbox17'].show()
            else:
                self.view['smsbody_textview'].get_buffer().set_text('')
                self.view['vbox17'].hide()

    def _send_sms_to_contact(self, menuitem, treeview):
        pass
#        selection = treeview.get_selection()
#        model, selected = selection.get_selected_rows()
#        iters = [model.get_iter(path) for path in selected]
#        numbers = [model.get_value(_iter, 2) for _iter in iters]
#
#        ctrl = NewSmsController(Model(), self)
#        view = NewSmsView(ctrl)
#        view.set_parent_view(self.view)
#        view.show()
#
#        ctrl.set_entry_text(", ".join(numbers))

    def _edit_external_contacts(self, menuitem, editor=None):
        if editor:
            cmd = editor[0]
            args = len(editor) > 1 and editor[1:] or []
            getProcessOutput(cmd, args, os.environ)

