"""Label images: a QR code that holds only a link, plus human-readable text.

Labels are drawn 696 pixels wide, which is the printable width of 62mm
Brother QL tape at 300 dpi, so the same image can be sent to the printer later.
"""

import io
import os

import qrcode
from PIL import Image, ImageDraw, ImageFont

LABEL_WIDTH = 696

FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/share/fonts/TTF",
    "/Library/Fonts",
]


def _font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        path = os.path.join(d, name)
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _qr(url, size):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    return img.resize((size, size), Image.NEAREST)


def _wrap(draw, text, font, max_width, max_lines):
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "...", font=font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "..."
    return lines


def _png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def device_label(url, code, type_name, status_letter, date_text=""):
    height = 290
    img = Image.new("L", (LABEL_WIDTH, height), 255)
    draw = ImageDraw.Draw(img)
    qr_size = 258
    img.paste(_qr(url, qr_size), (16, (height - qr_size) // 2))

    left = 16 + qr_size + 22
    badge = 112
    badge_x = LABEL_WIDTH - badge - 16
    # Status letter in a solid box, readable from across the bench.
    draw.rectangle([badge_x, 16, badge_x + badge, 16 + badge], fill=0)
    letter_font = _font(92, bold=True)
    w = draw.textlength(status_letter, font=letter_font)
    draw.text((badge_x + (badge - w) / 2, 16 + 4), status_letter, font=letter_font, fill=255)

    code_size = 62
    while code_size > 30 and draw.textlength(code, font=_font(code_size, bold=True)) > badge_x - left - 12:
        code_size -= 2
    draw.text((left, 22), code, font=_font(code_size, bold=True), fill=0)
    type_font = _font(30)
    y = 150
    for line in _wrap(draw, type_name, type_font, LABEL_WIDTH - left - 16, 2):
        draw.text((left, y), line, font=type_font, fill=0)
        y += 38
    if date_text:
        draw.text((left, height - 46), date_text, font=_font(24), fill=0)
    return _png(img)


def box_label(url, code, name):
    height = 380
    img = Image.new("L", (LABEL_WIDTH, height), 255)
    draw = ImageDraw.Draw(img)
    qr_size = 340
    img.paste(_qr(url, qr_size), (16, (height - qr_size) // 2))
    left = 16 + qr_size + 24
    draw.text((left, 24), "BOX", font=_font(34, bold=True), fill=0)
    draw.text((left, 70), code, font=_font(96, bold=True), fill=0)
    name_font = _font(36)
    y = 200
    for line in _wrap(draw, name, name_font, LABEL_WIDTH - left - 16, 3):
        draw.text((left, y), line, font=name_font, fill=0)
        y += 46
    return _png(img)
