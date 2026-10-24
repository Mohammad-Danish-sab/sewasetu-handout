import re


class OCRUnavailableError(RuntimeError):
    pass


def extract_age_proof_suggestions(content, extension):
    try:
        import fitz
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise OCRUnavailableError("Local OCR dependencies are unavailable.") from exc

    try:
        if extension == ".pdf":
            document = fitz.open(stream=content, filetype="pdf")
            pages = []
            for page in document[:3]:
                if page.rect.width * page.rect.height * 2.25 > 20_000_000:
                    raise OCRUnavailableError("The PDF page is too large for local OCR.")
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                pages.append(
                    Image.frombytes(
                        "RGB",
                        (pixmap.width, pixmap.height),
                        pixmap.samples,
                        "raw",
                        "RGB",
                        pixmap.stride,
                        1,
                    )
                )
        else:
            import io

            image = Image.open(io.BytesIO(content))
            if image.width * image.height > 20_000_000:
                raise OCRUnavailableError("The image is too large for local OCR.")
            image.thumbnail((2400, 2400))
            pages = [image.convert("RGB")]
        text = "\n".join(
            pytesseract.image_to_string(
                page, lang="eng+hin+asm", timeout=12
            )
            for page in pages
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        pytesseract.TesseractError,
        fitz.FileDataError,
        Image.DecompressionBombError,
    ) as exc:
        raise OCRUnavailableError("Could not read the document with local OCR.") from exc

    name_match = re.search(
        r"(?im)^\s*(?:name|नाम|নাম)\s*[:\-]?\s*([^\r\n]{2,80})",
        text,
    )
    name = name_match.group(1).strip() if name_match else ""
    if name:
        name = re.split(
            r"\b(?:dob|date of birth|birth date)\b|जन्म तिथि|জন্ম তাৰিখ",
            name,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" :-")
    dates = list(
        dict.fromkeys(
            re.findall(
                r"\b(?:0?[1-9]|[12][0-9]|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:19|20)\d{2}\b",
                text,
            )
        )
    )
    return {
        "name": name,
        "dates": dates[:5],
    }
