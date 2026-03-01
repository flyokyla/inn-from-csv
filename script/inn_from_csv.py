import asyncio
import os
import re
import pandas as pd
from playwright.async_api import async_playwright

URL = "https://service.nalog.ru/inn.do"

WAIT_INN_TIMEOUT_MS    = 120_000
WAIT_CAPTCHA_TIMEOUT_MS = 120_000

PAUSE_BETWEEN_REQUESTS = 10  # seconds between requests

CAPTCHA_TEXTS = [
    "ВВЕДИТЕ ЦИФРЫ С КАРТИНКИ",
    "Подтвердите, что Вы не робот",
    "Вы превысили лимит запросов",
]

NOT_FOUND_TEXTS = [
    "Информация об ИНН не найдена",
]

DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")

COL_INN_DEFAULT = "ИНН"


# ── Utility functions ────────────────────────────────────────────

def read_file(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(path, dtype=str, keep_default_na=False)
    # CSV with encoding auto-detection
    for enc in ("utf-8-sig", "utf-8", "cp1251", "windows-1251"):
        try:
            return pd.read_csv(path, sep=";", encoding=enc, dtype=str, keep_default_na=False)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, sep=";", encoding="utf-8", dtype=str, keep_default_na=False, errors="replace")


def to_ddmmyyyy(s: str) -> str:
    s = ("" if s is None else str(s)).strip()
    if not s:
        return ""
    if DATE_RE.fullmatch(s):
        return s
    dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
    return "" if pd.isna(dt) else dt.strftime("%d.%m.%Y")


def split_fio(fio: str):
    fio = ("" if fio is None else str(fio)).strip()
    parts = [p for p in fio.split() if p]
    fam  = parts[0] if len(parts) > 0 else ""
    nam  = parts[1] if len(parts) > 1 else ""
    otch = " ".join(parts[2:]) if len(parts) > 2 else ""
    return fam, nam, otch


def passport_digits(passport: str) -> str:
    raw = ("" if passport is None else str(passport)).strip()
    return re.sub(r"\D+", "", raw)


def format_docno(passport: str) -> str:
    """4523 329167 or 4523329167 -> '45 23 329167'"""
    digits = passport_digits(passport)
    if len(digits) == 10:
        series = digits[:4]
        number = digits[4:]
        return f"{series[:2]} {series[2:]} {number}"
    return ""


def row_has_enough_data(fio: str, bdate: str, passport: str) -> bool:
    fam, nam, _ = split_fio(fio)
    if not fam or not nam:
        return False
    bdate = to_ddmmyyyy(bdate)
    if not bdate:
        return False
    digits = passport_digits(passport)
    if len(digits) != 10:
        return False
    return True


# ── Browser interaction helpers ──────────────────────────────────

async def fast_fill(locator, value: str):
    value = "" if value is None else str(value)
    await locator.wait_for(state="visible")
    await locator.fill(value)


async def set_doctype_21(page):
    sel = page.locator('select[name="doctype"], select#doctype')
    if await sel.count() > 0:
        await sel.first.evaluate(
            """(el) => {
                el.value = "21";
                el.dispatchEvent(new Event("input", { bubbles: true }));
                el.dispatchEvent(new Event("change", { bubbles: true }));
            }"""
        )
        return

    try:
        label = page.get_by_text(re.compile(r"Вид документа", re.I)).first
        if await label.is_visible():
            await label.click()
    except Exception:
        pass

    option_variants = [
        page.get_by_role("option", name=re.compile(r"^\\s*21\\b.*паспорт", re.I)),
        page.get_by_text(re.compile(r"^\\s*21\\b.*Паспорт гражданина Российской Федерации", re.I)),
        page.get_by_text(re.compile(r"^\\s*21\\b", re.I)),
    ]
    for opt in option_variants:
        try:
            await opt.first.wait_for(state="visible", timeout=3000)
            await opt.first.click()
            return
        except Exception:
            continue

    raise RuntimeError("Не смог выбрать doctype=21 (Паспорт РФ)")


async def click_submit(page):
    candidates = [
        page.locator("#btn_send"),
        page.locator('button[type="submit"]'),
        page.locator('input[type="submit"]'),
        page.get_by_role("button", name=re.compile(r"отправ", re.I)),
        page.get_by_role("button", name=re.compile(r"найти|запрос|получ", re.I)),
    ]
    for loc in candidates:
        try:
            if await loc.count() > 0 and await loc.first.is_visible():
                await loc.first.click()
                return
        except Exception:
            continue
    raise RuntimeError("Не нашёл кнопку отправки")


def captcha_js_regex():
    escaped = [re.escape(t) for t in CAPTCHA_TEXTS]
    return "(" + "|".join(escaped) + ")"


async def wait_captcha(page):
    rgx = captcha_js_regex()
    await page.wait_for_function(
        f"""() => {{
            const t = document.body ? (document.body.innerText || "") : "";
            return /{rgx}/i.test(t);
        }}""",
        timeout=WAIT_CAPTCHA_TIMEOUT_MS
    )
    return True


def not_found_js_regex():
    escaped = [re.escape(t) for t in NOT_FOUND_TEXTS]
    return "(" + "|".join(escaped) + ")"


async def wait_not_found(page):
    rgx = not_found_js_regex()
    await page.wait_for_function(
        f"""() => {{
            const t = document.body ? (document.body.innerText || "") : "";
            return /{rgx}/i.test(t);
        }}""",
        timeout=WAIT_INN_TIMEOUT_MS
    )
    return True


async def wait_for_inn_from_network(page, timeout_ms: int) -> str:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    async def process_response(resp):
        try:
            if "inn-new-proc.json" not in resp.url:
                return
            req = resp.request
            if req.method != "POST":
                return
            post = req.post_data or ""
            if "c=get" not in post:
                return
            data = await resp.json()
            if data.get("state") == 1 and data.get("inn"):
                if not fut.done():
                    fut.set_result(data["inn"])
        except Exception:
            return

    def handler(resp):
        asyncio.create_task(process_response(resp))

    page.on("response", handler)
    try:
        return await asyncio.wait_for(fut, timeout=timeout_ms / 1000.0)
    finally:
        try:
            page.remove_listener("response", handler)
        except Exception:
            pass


async def fill_form(page, fam, nam, otch, bdate, docno, docdt):
    fam_in   = page.locator('input[name="fam"], input#fam').first
    nam_in   = page.locator('input[name="nam"], input#nam').first
    otch_in  = page.locator('input[name="otch"], input#otch').first
    bdate_in = page.locator('input[name="bdate"], input#bdate').first
    docno_in = page.locator('input[name="docno"], input#docno').first
    docdt_in = page.locator('input[name="docdt"], input#docdt').first

    await fam_in.wait_for(state="visible", timeout=15000)

    await fast_fill(fam_in, fam)
    await fast_fill(nam_in, nam)
    await fast_fill(otch_in, otch)
    await fast_fill(bdate_in, bdate)

    await set_doctype_21(page)

    await fast_fill(docno_in, docno)

    if docdt:
        await fast_fill(docdt_in, docdt)


# ── Core processing function (used by both CLI and web) ──────────

async def process_dataframe(
    df: pd.DataFrame,
    output_path: str,
    col_fio: str,
    col_bdate: str,
    col_passport: str,
    col_docdt: str = "",
    col_inn: str = COL_INN_DEFAULT,
    on_progress=None,
    on_captcha=None,
    stop_event: asyncio.Event = None,
):
    """
    Process a DataFrame to fetch INN numbers from the FNS website.

    Args:
        df: DataFrame with person data
        output_path: path to save result CSV
        col_fio: column name for full name
        col_bdate: column name for birth date
        col_passport: column name for passport series+number
        col_docdt: column name for passport issue date (empty = skip)
        col_inn: column name where INN values will be written (default "ИНН")
        on_progress: async callback(idx, total, status, fio, inn)
            status: "skip" | "ok" | "not_found" | "error" | "captcha" | "processing"
        on_captcha: async callable that waits until captcha is solved (replaces input())
        stop_event: if set, stops processing
    """
    df.columns = [str(c).strip() for c in df.columns]

    has_docdt = bool(col_docdt) and col_docdt in df.columns

    if col_inn not in df.columns:
        df[col_inn] = ""

    ok = 0
    skipped = 0
    fail = 0

    async with async_playwright() as p:
        user_data_dir = os.path.join(os.path.dirname(output_path) or ".", "pw_nalog_profile")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            locale="ru-RU",
        )
        page = await context.new_page()

        total = len(df)
        for idx, row in df.iterrows():
            if stop_event and stop_event.is_set():
                break

            # Already has INN — skip
            if str(row.get(col_inn, "")).strip():
                continue

            fio       = row[col_fio]
            bdate_raw = row[col_bdate]
            pass_raw  = row[col_passport]
            docdt_raw = row[col_docdt] if has_docdt else ""

            if not row_has_enough_data(fio, bdate_raw, pass_raw):
                df.at[idx, col_inn] = "-"
                skipped += 1
                df.to_excel(output_path, index=False, engine="openpyxl")
                if on_progress:
                    await on_progress(idx, total, "skip", fio, "-")
                continue

            fam, nam, otch = split_fio(fio)
            bdate = to_ddmmyyyy(bdate_raw)
            docno = format_docno(pass_raw)
            docdt = to_ddmmyyyy(docdt_raw)

            if on_progress:
                await on_progress(idx, total, "processing", fio, "")

            try:
                await page.goto(URL, wait_until="domcontentloaded")
                await fill_form(page, fam, nam, otch, bdate, docno, docdt)

                while True:
                    if stop_event and stop_event.is_set():
                        break

                    inn_task       = asyncio.create_task(wait_for_inn_from_network(page, WAIT_INN_TIMEOUT_MS))
                    captcha_task   = asyncio.create_task(wait_captcha(page))
                    not_found_task = asyncio.create_task(wait_not_found(page))

                    await click_submit(page)

                    done, _ = await asyncio.wait(
                        {inn_task, captcha_task, not_found_task},
                        return_when=asyncio.FIRST_COMPLETED
                    )

                    if inn_task in done and not inn_task.cancelled():
                        inn = inn_task.result()
                        df.at[idx, col_inn] = inn
                        ok += 1
                        df.to_excel(output_path, index=False, engine="openpyxl")
                        captcha_task.cancel()
                        not_found_task.cancel()
                        if on_progress:
                            await on_progress(idx, total, "ok", fio, inn)
                        await asyncio.sleep(PAUSE_BETWEEN_REQUESTS)
                        break

                    if not_found_task in done:
                        inn_task.cancel()
                        captcha_task.cancel()
                        df.at[idx, col_inn] = "-"
                        skipped += 1
                        df.to_excel(output_path, index=False, engine="openpyxl")
                        if on_progress:
                            await on_progress(idx, total, "not_found", fio, "-")
                        await asyncio.sleep(PAUSE_BETWEEN_REQUESTS)
                        break

                    if captcha_task in done:
                        inn_task.cancel()
                        not_found_task.cancel()
                        df.to_excel(output_path, index=False, engine="openpyxl")
                        if on_progress:
                            await on_progress(idx, total, "captcha", fio, "")
                        if on_captcha:
                            await on_captcha()
                        else:
                            print("Капча/лимит. Реши в браузере и нажми Enter...")
                            await asyncio.to_thread(input)
                        continue

            except Exception as e:
                df.at[idx, col_inn] = f"ERROR: {e}"
                fail += 1
                df.to_excel(output_path, index=False, engine="openpyxl")
                if on_progress:
                    await on_progress(idx, total, "error", fio, str(e))

        await context.close()

    return {"ok": ok, "skipped": skipped, "fail": fail}


# ── Standalone CLI mode ──────────────────────────────────────────

async def main():
    INPUT_CSV  = "инн-норм.csv"
    OUTPUT_CSV = "инн-норм_with_inn.csv"

    df = read_file(OUTPUT_CSV) if os.path.exists(OUTPUT_CSV) and os.path.getsize(OUTPUT_CSV) > 0 else read_file(INPUT_CSV)

    col_fio      = "фио"
    col_bdate    = "дата рождения"
    col_passport = "паспорт"
    col_docdt    = "дата выдачи паспорта"

    async def cli_progress(idx, total, status, fio, inn):
        labels = {
            "skip": "SKIP",
            "ok": "OK",
            "not_found": "НЕ НАЙДЕН",
            "error": "ERR",
            "captcha": "КАПЧА",
            "processing": "...",
        }
        label = labels.get(status, status)
        print(f"[{idx+1}/{total}] {label}  {fio}  {inn}")

    result = await process_dataframe(
        df, OUTPUT_CSV, col_fio, col_bdate, col_passport, col_docdt,
        on_progress=cli_progress,
    )
    print(f"\nГотово. OK: {result['ok']}, пропущено: {result['skipped']}, ошибок: {result['fail']}")
    print(f"Результат: {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
