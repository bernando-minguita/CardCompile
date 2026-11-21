"""Background workers so imports, exports and image corrections never freeze the interface."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QMutex, QMutexLocker, QThread, QWaitCondition, Signal

from core.image_service import correct_image, load_rgb
from core.import_service import ImportPreview, ImportService
from core.logging_setup import get_logger
from core.pdf_service import PdfExportCancelled

LOGGER = get_logger("workers")


class ImportWorker(QThread):
    """Analyse (validate, hash, thumbnail) a batch of files off the UI thread."""

    progress = Signal(int, int, str)
    completed = Signal(list)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(
        self,
        service: ImportService,
        paths: Sequence[str],
        extensions: Sequence[str],
        duplicate_policy: str = "ask",
        existing_hashes: dict[str, list[str]] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._paths = list(paths)
        self._extensions = list(extensions)
        self._duplicate_policy = duplicate_policy
        self._hashes = existing_hashes

    def run(self) -> None:  # noqa: D102 - QThread API
        try:
            previews: list[ImportPreview] = self._service.analyze(
                self._paths,
                self._extensions,
                duplicate_policy=self._duplicate_policy,
                progress=self.progress.emit,
                is_cancelled=self.isInterruptionRequested,
                existing_hashes=self._hashes,
            )
            if self.isInterruptionRequested():
                # A user-initiated cancellation must never look like a finished
                # (possibly partial) analysis; emit a dedicated signal instead.
                self.cancelled.emit()
                return
            self.completed.emit(previews)
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.exception("Import analysis failed")
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        self.requestInterruption()


class ExportWorker(QThread):
    """Run the PDF export and its validation off the UI thread."""

    progress = Signal(int, int)
    completed = Signal(object)
    cancelled = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        pdf_service,
        page_ids: Sequence[str],
        output_path: str,
        options,
        validator=None,
        expected_sizes=None,
        separate: bool = False,
        base_name: str = "document",
        category_id: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = pdf_service
        self._page_ids = list(page_ids)
        self._output_path = output_path
        self._options = options
        self._validator = validator
        self._expected_sizes = expected_sizes
        self._separate = separate
        self._base_name = base_name
        self._category_id = category_id
        self._cancelled = False

    def run(self) -> None:  # noqa: D102 - QThread API
        try:
            self._options.cancel_check = self.isInterruptionRequested
            self._options.progress = self.progress.emit
            if self._separate:
                result = self._service.export_pages_separately(
                    self._page_ids, self._output_path, self._base_name, self._options
                )
            else:
                result = self._service.export_pages(
                    self._page_ids,
                    self._output_path,
                    self._options,
                    self._category_id,
                )
            if getattr(result, "cancelled", False):
                # A separate export stopped between pages: hand the partial
                # result to the dialog so it can list the files already written.
                self.cancelled.emit(result)
                return
            self.completed.emit(result)
        except PdfExportCancelled:
            LOGGER.info("PDF export cancelled by the user")
            self.cancelled.emit(None)
        except Exception as exc:
            LOGGER.exception("PDF export failed")
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        self._cancelled = True
        self.requestInterruption()


class CorrectionWorker(QThread):
    """Render corrected previews off the UI thread, newest request wins.

    Dragging a corner or a lighting slider produces a burst of requests.  A
    ``QThread`` per event would spawn dozens of threads and the UI would fall
    behind the newest state; this worker keeps exactly one pending payload and
    drops the intermediate ones, so the preview always converges on what the
    user is currently looking at.
    """

    rendered = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mutex = QMutex()
        self._condition = QWaitCondition()
        self._pending: tuple[int, dict] | None = None
        self._stopping = False

    def submit(self, token: int, payload: dict) -> None:
        """Queue *payload*; any request not yet started is discarded."""
        with QMutexLocker(self._mutex):
            self._pending = (int(token), payload)
            self._condition.wakeAll()

    def stop(self) -> None:
        """Ask the thread to finish; call :meth:`wait` afterwards."""
        with QMutexLocker(self._mutex):
            self._stopping = True
            self._condition.wakeAll()

    def run(self) -> None:  # pragma: no cover - threading behaviour
        while True:
            with QMutexLocker(self._mutex):
                while self._pending is None and not self._stopping:
                    self._condition.wait(self._mutex, 250)
                if self._stopping:
                    return
                token, payload = self._pending
                self._pending = None
            try:
                result = render_correction(payload)
            except Exception as exc:
                LOGGER.exception("Correction render failed")
                self.failed.emit(token, str(exc))
                continue
            self.rendered.emit(token, result)


def render_correction(payload: dict) -> dict:
    """Build one corrected preview image from a correction payload.

    Kept as a module-level function (rather than a method) so it can be called
    directly from tests without starting a thread.

    The payload carries the *source path* rather than a decoded image: the
    worker downscales before warping, so a 6000x4000 phone photo never has to be
    decoded at full resolution just to draw a preview.
    """
    source = Path(payload["path"])
    max_side = int(payload.get("max_side", 1200))
    quad = payload.get("quad")
    image = load_rgb(source)
    if max_side > 0 and max(image.size) > max_side:
        # Downscale first: the warp resamples anyway, so nothing visible is
        # lost and the expensive step runs on far fewer pixels.
        image.thumbnail((max_side, max_side))
    corrected = correct_image(
        image,
        quad=tuple(quad) if quad else None,
        brightness=float(payload.get("brightness", 1.0)),
        contrast=float(payload.get("contrast", 1.0)),
        saturation=float(payload.get("saturation", 1.0)),
        sharpness=float(payload.get("sharpness", 1.0)),
        shadow=bool(payload.get("shadow", False)),
    )
    return {
        "image": corrected,
        "source_size": (image.width, image.height),
        "size": (corrected.width, corrected.height),
    }

