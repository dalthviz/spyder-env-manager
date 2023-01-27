# -*- coding: utf-8 -*-
#
# ----------------------------------------------------------------------------
# Copyright © 2022, Spyder Development Team and spyder-env-manager contributors
#
# Licensed under the terms of the MIT license
# ----------------------------------------------------------------------------

"""
Spyder env manager Main Plugin Widget.
"""

# Standard library imports
import os
import os.path as osp
from pathlib import Path
from string import Template

# Third party imports
from envs_manager.backends.conda_like_interface import CondaLikeInterface
from envs_manager.manager import Manager
import qtawesome as qta
from qtpy.QtCore import QThread, QUrl
from qtpy.QtGui import QColor
from qtpy.QtWebEngineWidgets import WEBENGINE, QWebEnginePage
from qtpy.QtWidgets import (
    QComboBox,
    QDialog,
    QMessageBox,
    QSizePolicy,
    QStackedLayout,
)

# Spyder and local imports
from spyder.api.translations import get_translation
from spyder.api.widgets.main_widget import PluginMainWidget, PluginMainWidgetActions
from spyder.config.base import get_module_source_path
from spyder.dependencies import SPYDER_KERNELS_REQVER
from spyder.utils.icon_manager import ima
from spyder.utils.palette import QStylePalette
from spyder.widgets.browser import FrameWebView

from spyder_env_manager.spyder.config import (
    DEFAULT_BACKENDS_ROOT_PATH,
    conda_like_executable,
)
from spyder_env_manager.spyder.workers import EnvironmentManagerWorker
from spyder_env_manager.spyder.widgets.helper_widgets import (
    CustomParametersDialog,
    CustomParametersDialogWidgets,
)
from spyder_env_manager.spyder.widgets.packages_table import (
    EnvironmentPackagesActions,
    EnvironmentPackagesTable,
)

# Localization
_ = get_translation("spyder")


# =============================================================================
# ---- Constants
# =============================================================================
PLUGINS_PATH = get_module_source_path("spyder", "plugins")
TEMPLATES_PATH = Path(__file__) / "spyder" / "assets" / "templates"
MAIN_BG_COLOR = QStylePalette.COLOR_BACKGROUND_1
separador = osp.sep
ENVIRONMENT_MESSAGE = Path(
    separador.join(osp.dirname(os.path.abspath(__file__)).split(separador)[:-2]),
    "spyder",
    "assets",
    "templates",
    "environment_info.html",
)
CSS_PATH = Path(PLUGINS_PATH) / "help" / "utils" / "static" / "css"
SPYDER_KERNELS_VERSION = SPYDER_KERNELS_REQVER.split(";")[0]


class SpyderEnvManagerWidgetActions:
    # Triggers
    SelectEnvironment = "select_environment"
    NewEnvironment = "new_environment"
    DeleteEnvironment = "delete_environment"
    InstallPackage = "install_package"
    ListPackages = "list_packages"

    # Options menu actions
    ImportEnvironment = "import_environment_action"
    ExportEnvironment = "export_environment_action"
    ToggleExcludeDependency = "exclude_dependency_action"


class SpyderEnvManagerWidgetOptionsMenuSections:
    Display = "excludes_section"
    Highlight = "highlight_section"


class SpyderEnvManagerWidgetMainToolBarSections:
    Main = "main_section"


# =============================================================================
# ---- Widgets
# =============================================================================


class SpyderEnvManagerWidget(PluginMainWidget):

    # PluginMainWidget class constants
    ENABLE_SPINNER = True
    NO_ENVIRONMENTS_AVAILABLE = _("No environments available")

    def __init__(self, name=None, plugin=None, parent=None):
        super().__init__(name, plugin, parent)

        envs, _ = Manager.list_environments(
            backend=CondaLikeInterface.ID,
            root_path=self.get_conf("environments_path", DEFAULT_BACKENDS_ROOT_PATH),
            external_executable=self.get_conf(
                "conda_file_executable_path", conda_like_executable()
            ),
        )
        self.exclude_non_requested_packages = False
        self.env_manager_action_thread = QThread(None)
        self.manager_worker = None
        self._actions_enabled = True
        self.select_environment = QComboBox(self)
        self.select_environment.ID = SpyderEnvManagerWidgetActions.SelectEnvironment

        if not envs:
            self.envs_available = False
            self.select_environment.addItem(self.NO_ENVIRONMENTS_AVAILABLE, None)
        else:
            for env_name, env_directory in envs.items():
                self.select_environment.addItem(env_name, env_directory)
            self.envs_available = True

        self.select_environment.setToolTip("Select an environment")
        self.select_environment.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLength
        )
        self.select_environment.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        selected_environment = self.get_conf("selected_environment", None)
        if selected_environment:
            self.select_environment.setCurrentText(selected_environment)
        self.css_path = self.get_conf("css_path", CSS_PATH, "appearance")

        self.infowidget = FrameWebView(self)
        if WEBENGINE:
            self.infowidget.web_widget.page().setBackgroundColor(QColor(MAIN_BG_COLOR))
        else:
            self.infowidget.web_widget.setStyleSheet(
                "background:{}".format(MAIN_BG_COLOR)
            )
            self.infowidget.page().setLinkDelegationPolicy(
                QWebEnginePage.DelegateAllLinks
            )
        self.packages_table = EnvironmentPackagesTable(
            self, text_color=ima.MAIN_FG_COLOR
        )
        self.packages_table.sig_action_context_menu.connect(
            self.table_context_menu_actions
        )
        self.stack_layout = layout = QStackedLayout()
        layout.addWidget(self.infowidget)
        layout.addWidget(self.packages_table)
        self.setLayout(self.stack_layout)

        # Signals
        self.select_environment.currentIndexChanged.connect(self.source_changed)

    # ---- PluginMainWidget API
    # ------------------------------------------------------------------------
    def get_title(self):
        return _("Spyder Env Manager")

    def setup(self):
        # ---- Options menu actions

        exclude_dependency_action = self.create_action(
            SpyderEnvManagerWidgetActions.ToggleExcludeDependency,
            text=_("Exclude dependency packages"),
            tip=_("Exclude dependency packages"),
            toggled=self.update_packages,
        )

        import_environment_action = self.create_action(
            SpyderEnvManagerWidgetActions.ImportEnvironment,
            text=_("Import environment from file (.yml, .txt)"),
            tip=_("Import environment from file (.yml, .txt)"),
            triggered=self._message_import_environment,
        )

        export_environment_action = self.create_action(
            SpyderEnvManagerWidgetActions.ExportEnvironment,
            text=_("Export environment to file (.yml, .txt)"),
            tip=_("Export environment to file (.yml, .txt)"),
            triggered=self._message_export_environment,
        )

        # ---- Toolbar actions
        new_environment_action = self.create_action(
            SpyderEnvManagerWidgetActions.NewEnvironment,
            text=_("New environment"),
            icon=qta.icon("mdi.plus", color=ima.MAIN_FG_COLOR, rotated=270),
            triggered=self._message_new_environment,
        )

        delete_environment_action = self.create_action(
            SpyderEnvManagerWidgetActions.DeleteEnvironment,
            text=_("Delete environment"),
            icon=self.create_icon("editclear"),
            triggered=self._message_delete_environment,
        )

        install_package_action = self.create_action(
            SpyderEnvManagerWidgetActions.InstallPackage,
            text=_("Install package"),
            icon=qta.icon("mdi.view-grid-plus-outline", color=ima.MAIN_FG_COLOR),
            # mdi.toy-brick-plus-outline
            triggered=self._message_install_package,
        )

        # Options menu
        options_menu = self.get_options_menu()
        for item in [exclude_dependency_action]:
            self.add_item_to_menu(
                item,
                menu=options_menu,
                section=SpyderEnvManagerWidgetOptionsMenuSections.Highlight,
            )
        for item in [import_environment_action, export_environment_action]:
            self.add_item_to_menu(
                item,
                menu=options_menu,
                section=SpyderEnvManagerWidgetOptionsMenuSections.Display,
            )

        # Main toolbar
        main_toolbar = self.get_main_toolbar()

        for item in [
            self.select_environment,
            new_environment_action,
            install_package_action,
            delete_environment_action,
        ]:
            self.add_item_to_toolbar(
                item,
                toolbar=main_toolbar,
                section=SpyderEnvManagerWidgetMainToolBarSections.Main,
            )

        self.show_intro_message()
        self.source_changed()

    def show_intro_message(self):
        """Show introduction message on how to use the plugin."""
        intro_message_eq = _(
            "Click <span title='New environment' style='border : 0.5px solid #c0c0c0;'>&#xFF0B;</span> to create a new environment or to import an environment definition from a file , click the <span title='Options' style='border : 1px solid #c0c0c0;'>&#9776;</span> button on the top right too."
        )
        self.mainMessage = self._create_info_environment_page(
            title="Usage", message=intro_message_eq
        )
        self.infowidget.setHtml(self.mainMessage, QUrl.fromLocalFile(self.css_path))

    def update_font(self, rich_font):
        self._rich_font = rich_font
        self.infowidget.set_font(rich_font)

    def table_context_menu_actions(self, action, package_info):
        if action == EnvironmentPackagesActions.UpdatePackage:
            title = _("Update package")
            messages = _("Are you sure you want to update the selected package?")
            self._message_box(
                title,
                messages,
                action=EnvironmentPackagesActions.UpdatePackage,
                package_info=package_info,
            )
        elif action == EnvironmentPackagesActions.UninstallPackage:
            title = _("Uninstall package")
            messages = _("Are you sure you want to uninstall the selected package?")
            self._message_box(
                title,
                messages,
                action=EnvironmentPackagesActions.UninstallPackage,
                package_info=package_info,
            )
        elif action == EnvironmentPackagesActions.InstallPackageVersion:
            title = _("Change package version constraint")
            messages = ["Package", "Constraint", "Version"]
            types = ["Label", "ComboBox", "LineEditVersion"]
            contents = [
                {package_info["name"]},
                {"==", "<=", ">=", "<", ">", "latest"},
                {},
            ]
            self._message_box_editable(
                title,
                messages,
                contents,
                types,
                action=EnvironmentPackagesActions.InstallPackageVersion,
                package_info=package_info,
            )

    def source_changed(self):
        current_environment = self.select_environment.currentText()
        environments_available = current_environment != "No environments available"
        if environments_available:
            self._env_action(
                dialog=None, action=SpyderEnvManagerWidgetActions.ListPackages
            )
            self.stack_layout.setCurrentWidget(self.packages_table)
            # TODO: Set selected env interpreter as Spyder main interpreter
        else:
            self.stack_layout.setCurrentWidget(self.infowidget)
            self.stop_spinner()

    def update_actions(self):
        if self._actions_enabled:
            current_environment = self.select_environment.currentText()
            environments_available = current_environment != "No environments available"
            actions_ids = [
                SpyderEnvManagerWidgetActions.InstallPackage,
                SpyderEnvManagerWidgetActions.DeleteEnvironment,
                SpyderEnvManagerWidgetActions.ExportEnvironment,
            ]
            for action_id, action in self.get_actions().items():
                if action_id in actions_ids:
                    action.setEnabled(environments_available)
                else:
                    action.setEnabled(True)

    def update_packages(self, only_requested, packages=None):
        self.exclude_non_requested_packages = only_requested
        self.packages_table.load_packages(only_requested, packages)
        self.stop_spinner()

    def start_spinner(self):
        self._actions_enabled = False
        for action_id, action in self.get_actions().items():
            if action_id not in PluginMainWidgetActions.__dict__.values():
                action.setEnabled(False)
        self.select_environment.setDisabled(True)
        self.packages_table.setDisabled(True)
        super().start_spinner()

    def stop_spinner(self):
        self._actions_enabled = True
        self.update_actions()
        self.select_environment.setDisabled(False)
        self.packages_table.setDisabled(False)
        super().stop_spinner()

    def on_close(self):
        env_name = self.select_environment.currentText()
        self.set_conf("selected_environment", env_name)
        if (
            self.env_manager_action_thread
            and self.env_manager_action_thread.isRunning()
        ):
            self.env_manager_action_thread.terminate()
            self.env_manager_action_thread.wait()

    # ---- Private API
    # ------------------------------------------------------------------------

    def _create_info_environment_page(self, title, message):
        """Create html page to show while the kernel is starting"""
        with open(ENVIRONMENT_MESSAGE) as templete_message:
            environment_message_template = Template(templete_message.read())
            page = environment_message_template.substitute(
                css_path=self.css_path, title=title, message=message
            )
        return page

    def _add_new_environment_entry(self, manager, action_result, result_message):
        if action_result:
            if not self.envs_available:
                self.select_environment.clear()
            self.select_environment.addItem(manager.env_name, manager.env_directory)
            self.select_environment.setCurrentText(manager.env_name)
            self.set_conf("selected_environment", manager.env_name)
            self.envs_available = True
        else:
            self._message_error_box(result_message)
        self.stop_spinner()

    def _add_new_environment_entry_from_import(
        self, manager, action_result, result_message
    ):
        if action_result:
            # Add new imported environment entry
            self._add_new_environment_entry(manager, action_result, result_message)
            # Install needed spyder-kernels version
            packages = [f"spyder-kernels{SPYDER_KERNELS_VERSION}"]
            self._run_env_action(
                manager,
                manager.install,
                self.source_changed,
                packages,
                force=True,
            )
        else:
            self._message_error_box(result_message)
        self.stop_spinner()

    def _export_environment(self, manager, action_result, result_message):
        if action_result:
            QMessageBox.information(
                self,
                _("Environment exported"),
                _(f"Python Environment {manager.env_name} was exported."),
            )
        else:
            self._message_error_box(result_message)
        self.stop_spinner()

    def _install_package(self, manager, action_result, result_message):
        if action_result:
            self.source_changed()
        else:
            self._message_error_box(result_message)
        self.stop_spinner()

    def _delete_environment(self, manager, action_result, result_message):
        if action_result:
            env_name = self.select_environment.currentIndex()
            self.select_environment.removeItem(env_name)
            if self.select_environment.count() == 0:
                self.envs_available = False
                self.select_environment.addItem(self.NO_ENVIRONMENTS_AVAILABLE, None)
        else:
            self._message_error_box(result_message)
        self.stop_spinner()

    def _list_environment_packages(self, manager, action_result, result_message):
        if action_result:
            self.update_packages(
                self.exclude_non_requested_packages, result_message["packages"]
            )
        else:
            self._message_error_box(result_message)
        self.stop_spinner()

    def _run_env_action(
        self,
        manager,
        manager_action,
        on_ready,
        *manager_action_args,
        **manager_action_kwargs,
    ):
        if (
            self.env_manager_action_thread
            and self.env_manager_action_thread.isRunning()
        ):
            self.env_manager_action_thread.quit()
            self.env_manager_action_thread.wait()

        self.manager_worker = EnvironmentManagerWorker(
            self, manager, manager_action, *manager_action_args, **manager_action_kwargs
        )
        self.manager_worker.sig_ready.connect(on_ready)
        self.manager_worker.sig_ready.connect(self.env_manager_action_thread.quit)
        self.manager_worker.moveToThread(self.env_manager_action_thread)
        self.env_manager_action_thread.started.connect(self.manager_worker.start)
        self.start_spinner()
        self.env_manager_action_thread.start()

    def _package_action(self, package_info, dialog=None, action=None):
        root_path = Path(self.get_conf("environments_path"))
        external_executable = self.get_conf("conda_file_executable_path")
        backend = "conda-like"
        package_name = package_info["name"]
        if action == EnvironmentPackagesActions.UpdatePackage:
            env_name = self.select_environment.currentText()
            manager = Manager(
                backend,
                root_path=root_path,
                env_name=env_name,
                external_executable=external_executable,
            )
            self._run_env_action(
                manager,
                manager.update,
                self.source_changed,
                [package_name],
                force=True,
                capture_output=True,
            )
        elif action == EnvironmentPackagesActions.UninstallPackage:
            env_name = self.select_environment.currentText()
            manager = Manager(
                backend,
                root_path=root_path,
                env_name=env_name,
                external_executable=external_executable,
            )
            self._run_env_action(
                manager,
                manager.uninstall,
                self.source_changed,
                [package_name],
                force=True,
                capture_output=True,
            )
        elif dialog and action == EnvironmentPackagesActions.InstallPackageVersion:
            package_constraint = dialog.combobox.currentText()
            package_version = dialog.lineedit_version.text()
            packages = [f"{package_name}"]
            if package_constraint != "latest" and package_version:
                packages = [f"{package_name}{package_constraint}{package_version}"]
            env_name = self.select_environment.currentText()
            manager = Manager(
                backend,
                root_path=root_path,
                env_name=env_name,
                external_executable=external_executable,
            )
            self._run_env_action(
                manager,
                manager.install,
                self._install_package,
                packages,
                force=True,
            )
        else:
            self._message_error_box("Action unavailable at this moment.")

    def _env_action(self, dialog=None, action=None):
        root_path = Path(self.get_conf("environments_path"))
        external_executable = self.get_conf("conda_file_executable_path")
        backend = "conda-like"
        if dialog and action == SpyderEnvManagerWidgetActions.NewEnvironment:
            backend = dialog.combobox.currentText()
            env_name = dialog.lineedit_string.text()
            python_version = dialog.combobox_edit.currentText()
            packages = [
                f"python={python_version}",
                f"spyder-kernels{SPYDER_KERNELS_VERSION}",
            ]
            manager = Manager(
                backend,
                root_path=root_path,
                env_name=env_name,
                external_executable=external_executable,
            )
            self._run_env_action(
                manager,
                manager.create_environment,
                self._add_new_environment_entry,
                packages=packages,
                force=True,
            )
        elif dialog and action == SpyderEnvManagerWidgetActions.ImportEnvironment:
            backend = dialog.combobox.currentText()
            env_name = dialog.lineedit_string.text()
            import_file_path = dialog.file_combobox.combobox.currentText()
            manager = Manager(
                backend,
                root_path=root_path,
                env_name=env_name,
                external_executable=external_executable,
            )
            self._run_env_action(
                manager,
                manager.import_environment,
                self._add_new_environment_entry_from_import,
                import_file_path,
                force=True,
            )
        elif dialog and action == SpyderEnvManagerWidgetActions.InstallPackage:
            package_name = dialog.lineedit_string.text()
            package_constraint = dialog.combobox.currentText()
            package_version = dialog.lineedit_version.text()
            packages = [f"{package_name}"]
            if package_constraint != "latest" and package_version:
                packages = [f"{package_name}{package_constraint}{package_version}"]
            env_name = self.select_environment.currentText()
            manager = Manager(
                backend,
                root_path=root_path,
                env_name=env_name,
                external_executable=external_executable,
            )
            self._run_env_action(
                manager,
                manager.install,
                self._install_package,
                packages,
                force=True,
            )
        elif action == SpyderEnvManagerWidgetActions.DeleteEnvironment:
            env_name = self.select_environment.currentText()
            manager = Manager(
                backend,
                root_path=root_path,
                env_name=env_name,
                external_executable=external_executable,
            )
            self._run_env_action(
                manager,
                manager.delete_environment,
                self._delete_environment,
                force=True,
            )
        elif action == SpyderEnvManagerWidgetActions.ListPackages:
            env_directory = self.select_environment.currentData()
            if env_directory:
                manager = Manager(
                    backend,
                    env_directory=env_directory,
                    external_executable=external_executable,
                )
                self._run_env_action(
                    manager,
                    manager.list,
                    self._list_environment_packages,
                )
        elif dialog and action == SpyderEnvManagerWidgetActions.ExportEnvironment:
            backend = dialog.combobox.currentText()
            env_name = self.select_environment.currentText()
            export_file_path = dialog.file_lineedit.lineedit.text()
            manager = Manager(
                backend,
                root_path=root_path,
                env_name=env_name,
                external_executable=external_executable,
            )
            self._run_env_action(
                manager,
                manager.export_environment,
                self._export_environment,
                export_file_path=export_file_path,
            )
        else:
            self._message_error_box("Action unavailable at this moment.")

    def _message_export_environment(self):
        title = _("Export Python environment")
        messages = [_("Manager to use"), _("Environment file")]
        types = [
            CustomParametersDialogWidgets.ComboBox,
            CustomParametersDialogWidgets.LineEditFile,
        ]
        contents = [{"conda-like"}, {}]
        self._message_box_editable(
            title,
            messages,
            contents,
            types,
            action=SpyderEnvManagerWidgetActions.ExportEnvironment,
        )

    def _message_delete_environment(self):
        title = _("Delete environment")
        messages = _("Are you sure you want to delete the current environment?")
        self._message_box(
            title, messages, action=SpyderEnvManagerWidgetActions.DeleteEnvironment
        )

    def _message_import_environment(self):
        title = _("Import Python environment")
        messages = [_("Manager to use"), _("Environment name"), _("Environment file")]
        types = [
            CustomParametersDialogWidgets.ComboBox,
            CustomParametersDialogWidgets.LineEditString,
            CustomParametersDialogWidgets.ComboBoxFile,
        ]
        contents = [{"conda-like"}, {}, {}]
        self._message_box_editable(
            title,
            messages,
            contents,
            types,
            action=SpyderEnvManagerWidgetActions.ImportEnvironment,
        )

    def _message_new_environment(self):
        title = _("New Python environment")
        messages = ["Manager to use", "Environment Name", "Python version"]
        types = [
            CustomParametersDialogWidgets.ComboBox,
            CustomParametersDialogWidgets.LineEditString,
            CustomParametersDialogWidgets.ComboBoxEdit,
        ]
        contents = [
            {"conda-like"},
            {},
            ["3.7.15", "3.8.15", "3.9.15", "3.10.8"],
        ]
        self._message_box_editable(
            title,
            messages,
            contents,
            types,
            action=SpyderEnvManagerWidgetActions.NewEnvironment,
        )

    def _message_install_package(self):
        title = _("Install package")
        messages = ["Package", "Constraint", "Version"]
        types = [
            CustomParametersDialogWidgets.LineEditString,
            CustomParametersDialogWidgets.ComboBox,
            CustomParametersDialogWidgets.LineEditVersion,
        ]
        contents = [{}, ["==", "<=", ">=", "<", ">", "latest"], {}]
        self._message_box_editable(
            title,
            messages,
            contents,
            types,
            action=SpyderEnvManagerWidgetActions.InstallPackage,
        )

    def _message_box_editable(
        self, title, messages, contents, types, action=None, package_info=None
    ):
        box = CustomParametersDialog(
            self,
            title=title,
            messages=messages,
            types=types,
            contents=contents,
        )
        box.setMaximumWidth(box.width())
        box.setMaximumHeight(box.height())
        box.setMinimumWidth(box.width())
        box.setMinimumHeight(box.height())
        result = box.exec_()
        if result == QDialog.Accepted:
            if package_info:
                self._package_action(package_info, dialog=box, action=action)
            else:
                self._env_action(dialog=box, action=action)

    def _message_box(self, title, message, action=None, package_info=None):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Question)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        box.setText(message)
        result = box.exec_()
        if result == QMessageBox.Yes:
            if package_info:
                self._package_action(package_info, action=action)
            else:
                self._env_action(dialog=box, action=action)

    def _message_error_box(self, message):
        box = QMessageBox(self)
        box.setWindowTitle("Error message")
        box.setIcon(QMessageBox.Critical)
        box.setStandardButtons(QMessageBox.Ok)
        box.setDefaultButton(QMessageBox.Ok)
        box.setText(message)
        box.exec_()
