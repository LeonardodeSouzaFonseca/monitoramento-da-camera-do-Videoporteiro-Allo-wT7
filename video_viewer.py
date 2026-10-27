"""Standalone desktop live viewer for Intelbras Allo wT7."""

from __future__ import annotations

import asyncio
import ctypes
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any
from ctypes import wintypes

import aiohttp
import imageio_ffmpeg
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _load_integration_modules() -> tuple[ModuleType, ModuleType, ModuleType]:
    """Load protocol modules without importing Home Assistant's package setup."""
    component_path = Path(__file__).parent / "custom_components" / "allo_wt7"
    package_name = "_allo_wt7_standalone"
    package = ModuleType(package_name)
    package.__path__ = [str(component_path)]
    package.__package__ = package_name
    sys.modules[package_name] = package
    client = importlib.import_module(f"{package_name}.client")
    video = importlib.import_module(f"{package_name}.video")
    const = importlib.import_module(f"{package_name}.const")
    return client, video, const


CLIENT, VIDEO, CONST = _load_integration_modules()

_CREDENTIAL_TARGET = "AlloWT7Viewer/AlloPlus"
_CREDENTIAL_TYPE_GENERIC = 1
_CREDENTIAL_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class _WindowsCredential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _credential_api():
    if sys.platform != "win32":
        raise OSError("O cofre de credenciais só está disponível no Windows.")
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(_WindowsCredential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_WindowsCredential)),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def _save_windows_password(
    password: str, email: str, target: str = _CREDENTIAL_TARGET
) -> None:
    encoded = password.encode("utf-16-le")
    if len(encoded) > 5120:
        raise ValueError("A senha excede o limite do cofre de credenciais do Windows.")
    blob = ctypes.create_string_buffer(encoded)
    credential = _WindowsCredential(
        Type=_CREDENTIAL_TYPE_GENERIC,
        TargetName=target,
        CredentialBlobSize=len(encoded),
        CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
        Persist=_CREDENTIAL_PERSIST_LOCAL_MACHINE,
        UserName=email,
    )
    api = _credential_api()
    if not api.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def _read_windows_password(target: str = _CREDENTIAL_TARGET) -> str | None:
    api = _credential_api()
    credential = ctypes.POINTER(_WindowsCredential)()
    if not api.CredReadW(
        target,
        _CREDENTIAL_TYPE_GENERIC,
        0,
        ctypes.byref(credential),
    ):
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return None
        raise ctypes.WinError(error)
    try:
        item = credential.contents
        if not item.CredentialBlobSize:
            return ""
        raw = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
        return raw.decode("utf-16-le")
    finally:
        api.CredFree(credential)


def _delete_windows_password(target: str = _CREDENTIAL_TARGET) -> None:
    api = _credential_api()
    if not api.CredDeleteW(target, _CREDENTIAL_TYPE_GENERIC, 0):
        error = ctypes.get_last_error()
        if error != _ERROR_NOT_FOUND:
            raise ctypes.WinError(error)


def _settings_path() -> Path:
    app_data = Path(
        os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    )
    return app_data / "AlloWT7Viewer" / "settings.json"


async def _fetch_devices(email: str, password: str) -> list[Any]:
    async with aiohttp.ClientSession() as session:
        client = CLIENT.AlloWT7Client(
            session, client_id=CLIENT.generate_client_id()
        )
        return await client.fetch_devices(email, password)


async def _fetch_stream_credentials(
    email: str, password: str, umid: str
) -> Any:
    async with aiohttp.ClientSession() as session:
        client = CLIENT.AlloWT7Client(
            session, client_id=CLIENT.generate_client_id()
        )
        return await client.fetch_stream_credentials(email, password, umid)


class LoginWorker(QThread):
    devices_loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, email: str, password: str) -> None:
        super().__init__()
        self._email = email
        self._password = password

    def run(self) -> None:
        try:
            devices = asyncio.run(_fetch_devices(self._email, self._password))
        except Exception as err:
            self.failed.emit(str(err) or repr(err))
        else:
            self.devices_loaded.emit(devices)


class StreamWorker(QThread):
    connected = Signal(object, object, str)
    failed = Signal(str)

    def __init__(
        self,
        email: str,
        password: str,
        umid: str,
        host: str,
        port: int,
        channel: int,
    ) -> None:
        super().__init__()
        self._email = email
        self._password = password
        self._umid = umid
        self._host = host
        self._port = port
        self._channel = channel
        self.video = None

    def run(self) -> None:
        video = None
        try:
            creds = asyncio.run(
                _fetch_stream_credentials(self._email, self._password, self._umid)
            )
            video = VIDEO.WT7Video(
                self._host,
                self._port,
                channel=self._channel,
                stream=CONST.VIDEO_STREAM,
            )
            self.video = video
            url = video.stream_url(creds)
        except Exception as err:
            if video is not None:
                video.stop()
            self.failed.emit(str(err) or repr(err))
        else:
            self.connected.emit(video, creds, url)


class FrameReader(QThread):
    frame_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, url: str, width: int = 960, height: int = 540) -> None:
        super().__init__()
        self._url = url
        self._width = width
        self._height = height
        self._stopping = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None

    def run(self) -> None:
        frame_size = self._width * self._height * 3
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-i",
            self._url,
            "-an",
            "-vf",
            f"scale={self._width}:{self._height}",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        try:
            command[0] = self._ffmpeg_executable()
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            while not self._stopping.is_set():
                frame = bytearray()
                while len(frame) < frame_size and not self._stopping.is_set():
                    chunk = self._process.stdout.read(frame_size - len(frame))
                    if not chunk:
                        if not self._stopping.is_set():
                            raise RuntimeError(
                                "O FFmpeg encerrou a leitura do vídeo."
                            )
                        return
                    frame.extend(chunk)
                if self._stopping.is_set():
                    return
                image = QImage(
                    bytes(frame),
                    self._width,
                    self._height,
                    self._width * 3,
                    QImage.Format.Format_RGB888,
                ).copy()
                self.frame_ready.emit(image)
        except Exception as err:
            if not self._stopping.is_set():
                self.failed.emit(str(err) or repr(err))
        finally:
            if self._process is not None:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait()
                if self._process.stdout is not None:
                    self._process.stdout.close()

    def stop(self) -> None:
        self._stopping.set()
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
        if not self.wait(3000):
            if self._process is not None and self._process.poll() is None:
                self._process.kill()
            self.wait()

    @staticmethod
    def _ffmpeg_executable() -> str:
        return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


class VideoViewer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Intelbras Allo wT7 — Vídeo ao vivo")
        self.resize(1000, 760)

        self.email = QLineEdit()
        self.email.setPlaceholderText("E-mail da conta Allo Plus")
        self.password = QLineEdit()
        self.password.setPlaceholderText("Senha da conta Allo Plus")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.host = QLineEdit()
        self.host.setPlaceholderText("Ex.: 192.168.1.42")
        self.save_data = QCheckBox("Salvar e-mail, senha e IP neste computador")
        self.devices = QComboBox()
        self.channel = QComboBox()
        self.channel.addItem("Câmera 1 (canal 1)", 1)
        self.channel.addItem("Câmera 2 (canal 2)", 2)
        self.channel.addItem("Câmera 3 (canal 3)", 3)

        self.load_button = QPushButton("Buscar dispositivos")
        self.connect_button = QPushButton("Assistir")
        self.stop_button = QPushButton("Parar")
        self.stop_button.setEnabled(False)
        self.status = QLabel("Informe seus dados e busque os dispositivos.")
        self.status.setWordWrap(True)
        self.picture = QLabel("O vídeo aparecerá aqui")
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setMinimumSize(640, 360)
        self.picture.setStyleSheet("background: #111; color: #ddd;")

        form = QFormLayout()
        form.addRow("E-mail:", self.email)
        form.addRow("Senha:", self.password)
        form.addRow("IP do monitor:", self.host)
        form.addRow("", self.save_data)
        form.addRow("Dispositivo:", self.devices)
        form.addRow("Câmera:", self.channel)

        buttons = QHBoxLayout()
        buttons.addWidget(self.load_button)
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.stop_button)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addWidget(self.picture, stretch=1)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.login_worker: LoginWorker | None = None
        self.stream_worker: StreamWorker | None = None
        self.frame_reader: FrameReader | None = None
        self.video = None
        self._load_saved_data()
        self.load_button.clicked.connect(self.load_devices)
        self.connect_button.clicked.connect(self.connect_stream)
        self.stop_button.clicked.connect(self.stop_stream)
        self.save_data.toggled.connect(self._save_option_changed)
        self.email.editingFinished.connect(self._save_if_enabled)
        self.password.editingFinished.connect(self._save_if_enabled)
        self.host.editingFinished.connect(self._save_if_enabled)

    def _load_saved_data(self) -> None:
        path = _settings_path()
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as err:
            self.status.setText(f"Não foi possível ler os dados salvos: {err}")
            return

        self.email.setText(str(saved.get("email", "")))
        self.host.setText(str(saved.get("host", "")))
        try:
            password = _read_windows_password()
        except OSError as err:
            self.status.setText(f"Não foi possível acessar a senha salva: {err}")
            return
        if password is not None:
            self.password.setText(password)
            self.save_data.setChecked(True)
            self.status.setText("Dados salvos carregados deste computador.")

    def _save_option_changed(self, enabled: bool) -> None:
        if enabled:
            self._save_if_enabled()
            return
        try:
            _delete_windows_password()
            path = _settings_path()
            path.unlink(missing_ok=True)
        except OSError as err:
            self.status.setText(f"Não foi possível apagar os dados salvos: {err}")
            self.save_data.blockSignals(True)
            self.save_data.setChecked(True)
            self.save_data.blockSignals(False)
            return
        self.status.setText("Dados salvos removidos deste computador.")

    def _save_if_enabled(self) -> None:
        if not self.save_data.isChecked():
            return
        email = self.email.text().strip()
        password = self.password.text()
        host = self.host.text().strip()
        if not email or not password or not host:
            self.status.setText(
                "Preencha e-mail, senha e IP para salvar os dados."
            )
            return
        try:
            _save_windows_password(password, email)
            path = _settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {"email": email, "host": host},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except (OSError, ValueError) as err:
            self.status.setText(f"Não foi possível salvar os dados: {err}")
            return
        self.status.setText(
            "E-mail, senha e IP salvos neste computador. A senha fica protegida pelo Windows."
        )

    def load_devices(self) -> None:
        email = self.email.text().strip()
        password = self.password.text()
        if not email or not password:
            self.status.setText("Preencha o e-mail e a senha.")
            return
        self._save_if_enabled()
        self.load_button.setEnabled(False)
        self.status.setText("Conectando à conta Allo Plus...")
        self.login_worker = LoginWorker(email, password)
        self.login_worker.devices_loaded.connect(self.devices_received)
        self.login_worker.failed.connect(self.show_error)
        self.login_worker.finished.connect(
            lambda: self.load_button.setEnabled(True)
        )
        self.login_worker.start()

    def devices_received(self, devices: list[Any]) -> None:
        self.devices.clear()
        for device in devices:
            label = f"{device.name or 'Allo wT7'} — {device.model or device.umid}"
            self.devices.addItem(label, device.umid)
        self.status.setText(
            f"{len(devices)} dispositivo(s) encontrado(s). Informe o IP local."
        )

    def connect_stream(self) -> None:
        email = self.email.text().strip()
        password = self.password.text()
        host = self.host.text().strip()
        umid = self.devices.currentData()
        if not email or not password or not host or not umid:
            self.status.setText(
                "Preencha e-mail, senha e IP, e selecione um dispositivo."
            )
            return
        self._save_if_enabled()
        if self.video is not None:
            self.stop_stream()
        self.connect_button.setEnabled(False)
        self.load_button.setEnabled(False)
        self.status.setText(
            "Obtendo credenciais e iniciando o stream local; aguarde..."
        )
        self.stream_worker = StreamWorker(
            email, password, umid, host, 18554, self.channel.currentData()
        )
        self.stream_worker.connected.connect(self.stream_connected)
        self.stream_worker.failed.connect(self.show_error)
        self.stream_worker.finished.connect(
            lambda: self.connect_button.setEnabled(True)
        )
        self.stream_worker.start()

    def stream_connected(self, video: Any, _creds: Any, url: str) -> None:
        self.video = video
        self.frame_reader = FrameReader(url)
        self.frame_reader.frame_ready.connect(self.show_frame)
        self.frame_reader.failed.connect(self.show_error)
        self.frame_reader.start()
        self.stop_button.setEnabled(True)
        self.status.setText(
            f"Câmera {self.channel.currentData()} conectada. "
            "Este aparelho aceita apenas uma sessão de vídeo por vez."
        )

    def show_frame(self, image: QImage) -> None:
        pixmap = QPixmap.fromImage(image)
        self.picture.setPixmap(
            pixmap.scaled(
                self.picture.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def show_error(self, message: str) -> None:
        self.status.setText(f"Erro: {message}")

    def stop_stream(self) -> None:
        if self.frame_reader is not None:
            self.frame_reader.stop()
            self.frame_reader = None
        if self.video is not None:
            self.video.stop()
            self.video = None
        self.picture.setText("O vídeo aparecerá aqui")
        self.picture.setPixmap(QPixmap())
        self.stop_button.setEnabled(False)
        self.status.setText("Stream encerrado.")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.login_worker is not None and self.login_worker.isRunning():
            self.login_worker.wait()
        if self.stream_worker is not None and self.stream_worker.isRunning():
            self.stream_worker.wait()
            if self.video is None:
                self.video = self.stream_worker.video
        self.stop_stream()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = VideoViewer()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
