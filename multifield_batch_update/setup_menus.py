# Copyright 2019 Matthew Hayes

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os

from anki.hooks import addHook
from aqt.qt import QFileDialog, QStandardPaths
from aqt.utils import tooltip

from .dialogs.batch_update import BatchUpdateDialog
from .dialogs.change_log import ChangeLogDialog


def open_load_file_dialog(browser):
    nids = browser.selectedNotes()
    if nids:
        try:
            ext = ".csv"
            default_path = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
            path = os.path.join(default_path, f"changes{ext}")

            options = QFileDialog.Options()

            # native doesn't seem to works
            options |= QFileDialog.DontUseNativeDialog

            result = QFileDialog.getOpenFileName(
                browser, "Import CSV for Batch Update", path, f"CSV (*{ext})",
                options=options)

            if not isinstance(result, tuple):
                raise Exception("Expected a tuple from save dialog")
            file = result[0]
            if file:
                BatchUpdateDialog(browser, nids, file).exec_()

        except Exception as e:
            tooltip("Failed: {}".format(e))
    else:
        tooltip("You must select some cards first")


def open_changelog_dialog(browser):
    ChangeLogDialog(browser).exec_()


def setup_menus(browser):
    menu = browser.form.menuEdit
    menu.addSeparator()
    submenu = menu.addMenu("Multi-field Batch Update")
    action = submenu.addAction("Import CSV")
    action.triggered.connect(
        lambda _: open_load_file_dialog(browser))
    action = submenu.addAction("View Log")
    action.triggered.connect(
        lambda _: open_changelog_dialog(browser))


addHook("browser.setupMenus", setup_menus)
