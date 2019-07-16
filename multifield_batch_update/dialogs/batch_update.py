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

import csv
import difflib
import html
import os
import time
import traceback
from collections import defaultdict, namedtuple

from aqt.qt import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFontDatabase, QFrame, QHBoxLayout, QLabel,
                    QPlainTextEdit, QScrollArea, QSplitter, QStandardPaths, Qt, QVBoxLayout)
from aqt.utils import askUser

from ..db.change_log import ChangeLog, ChangeLogEntry

NOTHING_VALUE = "-Nothing-"

NoteChange = namedtuple("NoteChange", ["nid", "fld", "old", "new"])

DIFF_PRE = """<html>
<head>
<style>
p {
    font-family: "Lucida Console", Monaco, monospace;
}
ins {
    background-color: lightgreen;
    text-decoration: none;
}
del {
    background-color: lightpink;
    text-decoration: none;
}
</style>
</head>
<body>"""

DIFF_POST = """</body>
</html>
"""


class BatchUpdateError(Exception):
    """Thrown when unexpected error occurs"""
    pass


def html_diff(a, b):
    sm = difflib.SequenceMatcher(None, a, b)
    output = []
    for opcode, a0, a1, b0, b1 in sm.get_opcodes():
        if opcode == 'equal':
            output.append(sm.a[a0:a1])
        elif opcode == 'insert':
            output.append("<ins>" + sm.b[b0:b1] + "</ins>")
        elif opcode == 'delete':
            output.append("<del>" + sm.a[a0:a1] + "</del>")
        elif opcode == 'replace':
            output.append("<del>" + sm.a[a0:a1] + "</del>")
            output.append("<ins>" + sm.b[b0:b1] + "</ins>")
        else:
            raise BatchUpdateError("unexpected opcode")
    return ''.join(output)


class BatchUpdateDialog(QDialog):
    """Base class for dialogs"""

    def __init__(self, browser, nids, file):
        super().__init__(parent=browser)
        self.browser = browser
        self.nids = nids
        self.title = "Batch Update Selected Notes"
        self.changelog = ChangeLog()
        self.checkpoint_name = "Batch Update"
        self.file = file

        # note field names and model id
        first_note = self.browser.mw.col.getNote(self.nids[0])
        model = first_note.model()
        self.model_id = first_note.mid
        self.note_field_names = self.browser.mw.col.models.fieldNames(model)

        # file field names
        with open(self.file, encoding="utf-8") as inf:
            reader = csv.DictReader(inf)

            # load field names as list of strings
            self.file_field_names = reader.fieldnames

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(self.title)
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        vbox = QVBoxLayout()
        for row in self._ui_join_keys_row():
            vbox.addLayout(row)
        scroll_area = QScrollArea()
        inner = QFrame(scroll_area)
        vbox_scrollable = QVBoxLayout()
        inner.setLayout(vbox_scrollable)
        for row in self._ui_field_select_rows():
            vbox_scrollable.addLayout(row)
        scroll_area.setWidget(inner)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Vertical)
        splitter.addWidget(scroll_area)
        splitter.addWidget(self._ui_log())
        vbox.addWidget(splitter)
        vbox.addLayout(self._ui_bottom_row())

        self.setLayout(vbox)

    def _ui_join_keys_row(self):
        def _fix_width(cb):
            width = cb.minimumSizeHint().width()
            cb.view().setMinimumWidth(width)

        # first row consists of join keys for notes and file
        hbox = QHBoxLayout()
        hbox.setAlignment(Qt.AlignLeft)

        # file join key
        hbox.addWidget(QLabel("File Join Key:"))
        self.file_join_key_selection = QComboBox()
        self.file_join_key_selection.addItems(self.file_field_names)
        _fix_width(self.file_join_key_selection)
        if "nid" in self.file_field_names:
            self.file_join_key_selection.setCurrentText("nid")
        else:
            self.file_join_key_selection.setCurrentText(self.file_field_names[0])
        self.file_join_key_selection.currentIndexChanged.connect(
            lambda _: self._combobox_changed(self.file_join_key_selection))
        hbox.addWidget(self.file_join_key_selection)

        # note join key
        hbox.addWidget(QLabel("Note Join Key:"))
        self.note_join_key_selection = QComboBox()
        expanded_note_field_names = ["nid"] + self.note_field_names
        self.note_join_key_selection.addItems(expanded_note_field_names)
        _fix_width(self.note_join_key_selection)
        self.note_join_key_selection_default_value = "nid"
        if self.file_join_key_selection.currentText() in expanded_note_field_names:
            self.note_join_key_selection.setCurrentText(self.file_join_key_selection.currentText())
        else:
            self.note_join_key_selection.setCurrentText(self.note_join_key_selection_default_value)
        self.note_join_key_selection.currentIndexChanged.connect(
            lambda _: self._combobox_changed(self.note_join_key_selection))
        hbox.addWidget(self.note_join_key_selection)

        yield hbox

        hbox = QHBoxLayout()
        hbox.addWidget(QLabel("Define the mapping from file fields to note fields. "
                              "Any file fileds mapping to nothing will be ignored."))
        yield hbox

    def _ui_field_select_rows(self):

        # combo boxes to select mapping for remaining fields
        self.mapping_field_selections = []
        for field_name in self.file_field_names:
            # nid can only be used as a join key
            if field_name == "nid":
                continue
            hbox = QHBoxLayout()
            hbox.setAlignment(Qt.AlignLeft)
            hbox.addWidget(QLabel("{} -> ".format(field_name)))
            field_selection = QComboBox()
            field_selection.addItems([NOTHING_VALUE] + self.note_field_names)
            width = field_selection.minimumSizeHint().width()
            field_selection.view().setMinimumWidth(width)
            if field_name in self.note_field_names and \
                    field_name != self.note_join_key_selection.currentText():
                field_selection.setCurrentText(field_name)
            else:
                field_selection.setCurrentText(NOTHING_VALUE)
            field_selection.currentIndexChanged.connect(
                lambda _, fs=field_selection: self._combobox_changed(fs))
            hbox.addWidget(field_selection)
            self.mapping_field_selections.append(field_selection)
            yield hbox

    def _combobox_changed(self, updated_cb):
        new_text = updated_cb.currentText()

        # We only need to check for non-nothing values, because multiple fields can
        # be set to nothing.
        if new_text != NOTHING_VALUE and updated_cb is not self.file_join_key_selection:
            # We only need to check the note mappings.  We exclude the file join key combobox.
            note_field_combos = [self.note_join_key_selection] + self.mapping_field_selections

            for cb in note_field_combos:
                if cb is updated_cb:
                    continue
                else:
                    if cb.currentText() == new_text:
                        if cb is self.note_join_key_selection:
                            cb.setCurrentText(self.note_join_key_selection_default_value)
                        else:
                            cb.setCurrentText(NOTHING_VALUE)

    def _ui_log(self):
        self.log = QPlainTextEdit()
        self.log.setTabChangesFocus(False)
        self.log.setReadOnly(True)

        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(self.log.font().pointSize() - 2)
        self.log.setFont(font)
        return self.log

    def _ui_bottom_row(self):
        hbox = QHBoxLayout()

        buttons = QDialogButtonBox(Qt.Horizontal, self)

        # Button to check if content needs to be changed
        check_btn = buttons.addButton("&Dry-run",
                                      QDialogButtonBox.ActionRole)
        check_btn.setToolTip("Dry-run")
        check_btn.clicked.connect(lambda _: self.onCheck(mode="dryrun"))

        # Button to diff the proposed changes
        diff_btn = buttons.addButton("&Diff",
                                     QDialogButtonBox.ActionRole)
        diff_btn.setToolTip("Diff")
        diff_btn.clicked.connect(lambda _: self.onCheck(mode="diff"))

        # Button to make the proposed changes
        fix_btn = buttons.addButton("&Update",
                                    QDialogButtonBox.ActionRole)
        fix_btn.setToolTip("Update")
        fix_btn.clicked.connect(lambda _: self.onCheck(mode="update"))

        # Button to close this dialog
        close_btn = buttons.addButton("&Close",
                                      QDialogButtonBox.RejectRole)
        close_btn.clicked.connect(self.close)

        hbox.addWidget(buttons)
        return hbox

    def onCheck(self, *, mode):
        self.log.clear()
        try:
            # Mapping from field name in file to field combo boxes for notes.
            # We need to check each of the selections for the combo boxes.
            zipped_fields = zip(
                [fn for fn in self.file_field_names if fn != "nid"],
                self.mapping_field_selections)

            # mapping from file join key name to note join key name
            file_join_key_name = self.file_join_key_selection.currentText()
            note_join_key_name = self.note_join_key_selection.currentText()

            self.log.appendPlainText("Join key: File field '{}' -> Note field '{}'".format(
                file_join_key_name, note_join_key_name))

            # Check which of the field combo boxes having a non-nothing selection and
            # build the mapping from fields in file to fields in notes.
            file_to_note_mappings = {}
            for file_field_name, note_field_cb in zipped_fields:
                note_field_name = note_field_cb.currentText()
                if note_field_name != NOTHING_VALUE:
                    file_to_note_mappings[file_field_name] = note_field_name
                    self.log.appendPlainText("File field '{}' -> Note field '{}'".format(
                        file_field_name, note_field_name))
            if not file_to_note_mappings:
                self.log.appendPlainText("ERROR: No mappings selected")
                return

            # Check which key values exist and to make sure there are no duplicate values.
            # Build a mapping form these key values to the row which contains the field values.
            file_key_to_values = {}
            duplicate_file_key_values = set()
            with open(self.file, encoding="utf-8") as inf:
                reader = csv.DictReader(inf)
                for row in reader:
                    join_key_val = row[file_join_key_name]
                    if join_key_val in file_key_to_values:
                        duplicate_file_key_values.add(join_key_val)
                    else:
                        file_key_to_values[join_key_val] = row
            if duplicate_file_key_values:
                self.log.appendPlainText("ERROR: Found {} key values for '{}' that appear more than once:".format(
                    len(duplicate_file_key_values), file_join_key_name))
                for val in duplicate_file_key_values:
                    self.log.appendPlainText(val)
                return
            self.log.appendPlainText("Found {} records for '{}' in {}".format(
                len(file_key_to_values), file_join_key_name, self.file))

            # When we aren't joining by nid, we need to create an additional mapping from the join
            # key to the nid value, because we can only look up by nid.
            note_join_key_to_nid = {}
            if note_join_key_name != "nid":
                self.log.appendPlainText("Joining to notes by '{}', so finding all values.".format(
                    note_join_key_name))
                for nid in self.nids:
                    note = self.browser.mw.col.getNote(nid)
                    if note.mid != self.model_id:
                        self.log.appendPlainText(
                            "ERROR: Note {} has different model ID {} than expected {} based on first note. ".format(
                                nid, note.mid, self.model_id) + "Please only select notes of the same model.")
                        return
                    if note_join_key_name in note:
                        if note[note_join_key_name] in note_join_key_to_nid:
                            self.log.appendPlainText("ERROR: Value '{}' already exists in notes".format(
                                note[note_join_key_name]))
                            return
                        else:
                            note_join_key_to_nid[note[note_join_key_name]] = nid
                    else:
                        self.log.appendPlainText("ERROR: Field '{}' not found in note {}".format(
                            note_join_key_name, nid))
                        return

            # these store the changes we will propose to make (grouped by nid)
            note_changes = defaultdict(list)

            # track join keys that were not found in notes
            missing_note_keys = set()

            # how many fields being updated are empty
            empty_note_field_count = 0
            notes_with_empty_fields = set()

            for file_key, file_values in file_key_to_values.items():

                if note_join_key_name == "nid":
                    nid = file_key
                else:
                    if file_key in note_join_key_to_nid:
                        nid = note_join_key_to_nid[file_key]
                        self.log.appendPlainText("Found note {} with value {} for '{}'".format(
                            nid, file_key, note_join_key_name))
                    else:
                        self.log.appendPlainText("Could not find note with value {} for '{}'".format(
                            file_key, note_join_key_name))
                        missing_note_keys.add(file_key)
                        continue

                self.log.appendPlainText("Checking note {}".format(nid))

                try:
                    note = self.browser.mw.col.getNote(nid)
                except TypeError:
                    self.log.appendPlainText("ERROR: Note {} was not found".format(nid))
                    return

                # Get the current values for fields we're updating in the note.
                note_values = {}
                for note_field_name in file_to_note_mappings.values():
                    if note_field_name in note:
                        note_values[note_field_name] = note[note_field_name]
                    else:
                        self.log.appendPlainText("ERROR: Field '{}' not found in note {}".format(
                            note_field_name, nid))
                        return

                # Compare the file field values to the note field values and see if anything is different
                # and therefore needs to be updated.
                for file_field_name, note_field_name in file_to_note_mappings.items():
                    file_value = file_values[file_field_name]
                    note_value = note_values[note_field_name]
                    if file_value != note_value:
                        self.log.appendPlainText("Need to update note field '{}':".format(note_field_name))
                        self.log.appendPlainText("{}\n=>\n{}".format(
                            note_value or "<empty>", file_value))
                        note_changes[nid].append(NoteChange(nid=nid, fld=note_field_name,
                                                            old=note_value, new=file_value))
                        if not note_value:
                            empty_note_field_count += 1
                            notes_with_empty_fields.add(nid)

            if missing_note_keys:
                self.log.appendPlainText("ERROR: {} values were not found in notes for field '{}'".format(
                    len(missing_note_keys), note_join_key_name))
                return

            if note_changes:
                self.log.appendPlainText("Need to make changes to {} notes".format(
                    len(note_changes)))
                if empty_note_field_count:
                    self.log.appendPlainText("{} fields across {} notes are empty".format(
                        empty_note_field_count, len(notes_with_empty_fields)))

                if mode == "dryrun":
                    # nothing to do
                    pass
                elif mode == "diff":
                    ext = ".html"
                    default_path = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
                    path = os.path.join(default_path, f"diff{ext}")

                    options = QFileDialog.Options()

                    # native doesn't seem to works
                    options |= QFileDialog.DontUseNativeDialog

                    # we'll confirm ourselves
                    options |= QFileDialog.DontConfirmOverwrite

                    result = QFileDialog.getSaveFileName(
                        self, "Save HTML diff", path, f"HTML (*{ext})",
                        options=options)

                    if not isinstance(result, tuple):
                        raise Exception("Expected a tuple from save dialog")
                    file = result[0]
                    if file:
                        do_save = True
                        if not file.lower().endswith(ext):
                            file += ext
                        if os.path.exists(file):
                            if not askUser("{} already exists. Are you sure you want to overwrite it?".format(file),
                                           parent=self):
                                do_save = False
                        if do_save:
                            self.log.appendPlainText("Saving to {}".format(file))
                            with open(file, "w", encoding="utf-8") as outf:
                                outf.write(DIFF_PRE)
                                for nid, changes in note_changes.items():
                                    outf.write("<p>nid {}:</p>\n".format(nid))
                                    for change in changes:
                                        outf.write("<p>{}: {}</p>\n".format(
                                            change.fld,
                                            html_diff(html.escape(change.old),
                                                      html.escape(change.new))))
                                outf.write(DIFF_POST)
                            self.log.appendPlainText("Done")
                elif mode == "update":
                    if askUser("{} notes will be updated.  Are you sure you want to do this?".format(
                            len(note_changes)), parent=self):
                        self.log.appendPlainText("Beginning update")

                        self.browser.mw.checkpoint("{} ({} {})".format(
                            self.checkpoint_name, len(note_changes),
                            "notes" if len(note_changes) > 1 else "note"))
                        self.browser.model.beginReset()
                        updated_count = 0
                        try:
                            init_ts = int(time.time() * 1000)

                            for nid, changes in note_changes.items():
                                note = self.browser.mw.col.getNote(nid)
                                for change in changes:
                                    ts = int(time.time() * 1000)
                                    note[change.fld] = change.new
                                    self.changelog.record_change(
                                        "batch_update", init_ts,
                                        ChangeLogEntry(
                                            ts=ts, nid=nid, fld=change.fld,
                                            old=change.old, new=change.new))
                                note.flush()
                                updated_count += 1
                            self.log.appendPlainText("Updated {} notes".format(updated_count))
                        finally:
                            if updated_count:
                                self.changelog.commit_changes()
                                self.browser.mw.requireReset()
                            self.browser.model.endReset()
                else:
                    self.log.appendPlainText("ERROR: Unexpected mode: {}".format(mode))
                    return
            else:
                self.log.appendPlainText("No changes need to be made")

        except Exception:
            self.log.appendPlainText("Failed during dry run:\n{}".format(traceback.format_exc()))

        finally:
            # Ensure QPlainTextEdit refreshes (not clear why this is necessary)
            self.log.repaint()

    def close(self):
        self.changelog.close()
        super().close()
