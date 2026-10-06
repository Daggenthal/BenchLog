"""Direct printing to a Brother QL label printer (EXPERIMENTAL, not yet tried on a real printer).

Uses the brother_ql library, which talks to the printer directly without a
system driver. It is an optional install: see requirements-print.txt.
"""

import io

MODELS = ["QL-500", "QL-550", "QL-560", "QL-570", "QL-580N", "QL-600", "QL-650TD", "QL-700", "QL-710W",
          "QL-720NW", "QL-800", "QL-810W", "QL-820NWB", "QL-1050", "QL-1060N", "QL-1100", "QL-1110NWB"]

# key: (label shown, brother_ql label id, needs the two-colour flag)
TAPES = {
    "62": ("62mm continuous, black on white (DK-22205)", "62", False),
    "62red": ("62mm continuous, black and red on white (DK-22251)", "62", True),
}


def available():
    try:
        import brother_ql  # noqa: F401
        return True
    except ImportError:
        return False


def backend_for(address):
    if address.startswith("tcp://"):
        return "network"
    if address.startswith("usb://"):
        return "pyusb"
    if address.startswith("file://"):
        return "linux_kernel"
    raise RuntimeError("The printer address should start with file://, usb://, or tcp://.")


def raster(png_bytes, model, tape):
    """Turn a label image into the bytes the printer understands."""
    from PIL import Image
    from brother_ql.conversion import convert
    from brother_ql.raster import BrotherQLRaster

    if model not in MODELS:
        raise RuntimeError("Unknown printer model.")
    _label, label_id, red = TAPES.get(tape, TAPES["62"])
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    printer = BrotherQLRaster(model)
    printer.exception_on_warning = True
    return convert(qlr=printer, images=[image], label=label_id, rotate="0", threshold=70.0, dither=False,
                   compress=False, red=red, dpi_600=False, hq=True, cut=True)


def friendly(exc, address):
    text = str(exc)
    lowered = text.lower()
    if "permission" in lowered:
        return ("No permission to use the printer. Run: sudo usermod -aG lp $USER  then reboot, "
                "and check the printer's Editor Lite light is off.")
    if "no such file" in lowered or "not found" in lowered or "no such device" in lowered:
        return "No printer found at %s. Check that it is on, plugged in, and Editor Lite is off." % address
    if "refused" in lowered or "timed out" in lowered or "unreachable" in lowered:
        return "The printer at %s did not answer. Check its network address." % address
    return "Printing failed: %s" % text


def print_png(png_bytes, model, address, tape):
    """Print one label. Raises RuntimeError with a readable message on failure."""
    if not available():
        raise RuntimeError("Printing support is not installed yet. Install it from the System page.")
    from brother_ql.backends.helpers import send

    backend = backend_for(address)
    try:
        instructions = raster(png_bytes, model, tape)
        send(instructions=instructions, printer_identifier=address, backend_identifier=backend, blocking=True)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(friendly(exc, address))
