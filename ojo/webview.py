from gi.repository import WebKit2, GObject

import logging
from ojo import util
from ojo import ojoconfig


class WebView:
    def __init__(self):
        self.web_view = None
        self.is_loaded = False
        self.js_queue = []

    def add_to(self, widget):
        if not self.web_view:
            raise Exception("add_to called when WebView not yet loaded")
        widget.add(self.web_view)

    def grab_focus(self):
        if self.web_view:
            self.web_view.grab_focus()

    def js(self, command=None, commands=None):
        all_commands = []
        if command:
            all_commands.append(command)
        if commands:
            all_commands += commands

        if not all_commands and not self.js_queue:
            return

        if self.is_loaded:
            def _do_queue():
                batch = list(self.js_queue)
                self.js_queue.clear()
                for cmd in batch:
                    self.web_view.run_javascript(cmd, None, None, None)
                for cmd in all_commands:
                    self.web_view.run_javascript(cmd, None, None, None)

            GObject.idle_add(_do_queue)
        else:
            self.js_queue.extend(all_commands)

    def load(self, html_filename, on_load_fn=None, on_action_fn=None):
        self.web_view = WebKit2.WebView()
        self.web_view.set_can_focus(True)

        settings = self.web_view.get_settings()
        settings.set_enable_smooth_scrolling(True)
        settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)

        def nav(wv, dialog):
            try:
                command = dialog.get_message()
                if on_action_fn and command:
                    command = command[command.index("|") + 1:]
                    index = command.index(":")
                    action = command[:index]
                    argument = command[index + 1:]
                    on_action_fn(action, argument)
            except Exception:
                logging.exception("Error processing browser command")
            return True

        self.web_view.connect("script-dialog", nav)

        def _on_load(webview, event, *args):
            if event == WebKit2.LoadEvent.FINISHED:
                self.is_loaded = True
                if on_load_fn:
                    on_load_fn()

        self.web_view.connect("load-changed", _on_load)
        self.web_view.load_uri(util.path2url(ojoconfig.get_data_file(html_filename)))

        util.make_transparent(self.web_view)
        self.web_view.set_visible(True)
