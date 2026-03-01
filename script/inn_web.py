"""
Web UI for INN parser — localhost:1337
Upload CSV/XLSX → map columns → parse INN from FNS → download result.
"""
import asyncio
import json
import os
import sys
import uuid

import pandas as pd
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse

# Add script dir to path so we can import inn_from_csv
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inn_from_csv import read_file, process_dataframe

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
RESULT_DIR = os.path.join(BASE_DIR, "results")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(RESULT_DIR, exist_ok=True)

# Active tasks: task_id -> {events: [], captcha_event, stop_event, done, result_file}
tasks: dict = {}


# ── HTML Templates ───────────────────────────────────────────────

STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; background: #f0f2f5; color: #1a1a2e; min-height: 100vh; }
  .container { max-width: 720px; margin: 40px auto; padding: 0 20px; }
  h1 { font-size: 24px; margin-bottom: 8px; }
  .subtitle { color: #666; margin-bottom: 24px; font-size: 14px; }
  .card { background: #fff; border-radius: 12px; padding: 32px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 24px; }
  .upload-area { border: 2px dashed #ccc; border-radius: 12px; padding: 48px 24px; text-align: center;
                 cursor: pointer; transition: all 0.2s; }
  .upload-area:hover, .upload-area.dragover { border-color: #4361ee; background: #f0f4ff; }
  .upload-area input[type=file] { display: none; }
  .upload-area .icon { font-size: 48px; margin-bottom: 12px; }
  .upload-area p { color: #666; }
  .upload-area .formats { font-size: 12px; color: #999; margin-top: 8px; }
  label { display: block; font-weight: 600; margin-bottom: 6px; font-size: 14px; }
  select, input[type=text] { width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px;
           background: #fff; margin-bottom: 16px; }
  .optional-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .optional-row input[type=checkbox] { width: 18px; height: 18px; }
  button, .btn { display: inline-block; padding: 12px 28px; background: #4361ee; color: #fff; border: none;
                 border-radius: 8px; font-size: 15px; cursor: pointer; font-weight: 600; text-decoration: none;
                 transition: background 0.2s; }
  button:hover, .btn:hover { background: #3a56d4; }
  button:disabled { background: #aaa; cursor: not-allowed; }
  .btn-danger { background: #e74c3c; }
  .btn-danger:hover { background: #c0392b; }
  .btn-success { background: #27ae60; }
  .btn-success:hover { background: #219a52; }
  .progress-bar { width: 100%; height: 8px; background: #e0e0e0; border-radius: 4px; overflow: hidden; margin: 16px 0; }
  .progress-bar .fill { height: 100%; background: #4361ee; transition: width 0.3s; border-radius: 4px; }
  .stats { display: flex; gap: 24px; margin: 16px 0; flex-wrap: wrap; }
  .stat { text-align: center; }
  .stat .num { font-size: 28px; font-weight: 700; }
  .stat .lbl { font-size: 12px; color: #666; }
  .log { max-height: 400px; overflow-y: auto; font-family: 'Consolas', monospace; font-size: 13px;
         background: #1a1a2e; color: #e0e0e0; border-radius: 8px; padding: 16px; margin-top: 16px; }
  .log .ok { color: #2ecc71; }
  .log .skip { color: #f39c12; }
  .log .err { color: #e74c3c; }
  .log .captcha { color: #e74c3c; font-weight: bold; }
  .log .processing { color: #74b9ff; }
  .captcha-alert { background: #ffeaa7; border: 2px solid #f39c12; border-radius: 8px; padding: 16px;
                   margin: 16px 0; display: none; text-align: center; }
  .captcha-alert p { margin-bottom: 12px; font-weight: 600; }
  .actions { display: flex; gap: 12px; margin-top: 24px; align-items: center; }
  .filename { font-size: 13px; color: #666; margin-top: 8px; }
  .footer { text-align: center; color: #999; font-size: 12px; padding: 24px 0; }
</style>
"""

def page_upload():
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ИНН Парсер</title>{STYLE}</head><body>
<div class="container">
  <h1>ИНН Парсер</h1>
  <p class="subtitle">Загрузите файл с данными для получения ИНН с сайта ФНС</p>
  <div class="card">
    <form id="uploadForm" action="/upload" method="post" enctype="multipart/form-data">
      <div class="upload-area" id="dropArea" onclick="document.getElementById('fileInput').click()">
        <div class="icon">📁</div>
        <p>Перетащите файл сюда или нажмите для выбора</p>
        <p class="formats">CSV (разделитель ;) или XLSX</p>
        <input type="file" id="fileInput" name="file" accept=".csv,.xlsx,.xls" required>
      </div>
      <p class="filename" id="fileName"></p>
    </form>
  </div>
</div>
<script>
const area = document.getElementById('dropArea');
const fi = document.getElementById('fileInput');
const fn = document.getElementById('fileName');
area.addEventListener('dragover', e => {{ e.preventDefault(); area.classList.add('dragover'); }});
area.addEventListener('dragleave', () => area.classList.remove('dragover'));
area.addEventListener('drop', e => {{
  e.preventDefault(); area.classList.remove('dragover');
  fi.files = e.dataTransfer.files;
  fi.dispatchEvent(new Event('change'));
}});
fi.addEventListener('change', () => {{
  if (fi.files.length) {{
    fn.textContent = fi.files[0].name;
    document.getElementById('uploadForm').submit();
  }}
}});
</script>
<div class="footer">prod. by @fourapm</div>
</body></html>""")


def page_mapping(filename: str, columns: list):
    opts = "".join(f'<option value="{c}">{c}</option>' for c in columns)
    # Default output name: original filename (without uuid prefix and extension) + _with_inn
    # filename format: "abcd1234_original.csv" -> take part after first underscore, strip extension
    parts = filename.split("_", 1)
    original_base = os.path.splitext(parts[1])[0] if len(parts) > 1 else os.path.splitext(filename)[0]
    default_output = f"{original_base}_with_inn"

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Выбор колонок — ИНН Парсер</title>{STYLE}</head><body>
<div class="container">
  <h1>Выбор колонок</h1>
  <p class="subtitle">Файл: {filename}</p>
  <div class="card">
    <form action="/start" method="post">
      <input type="hidden" name="filename" value="{filename}">

      <label>ФИО (фамилия, имя, отчество) *</label>
      <select name="col_fio" required>{opts}</select>

      <label>Дата рождения *</label>
      <select name="col_bdate" required>{opts}</select>

      <label>Серия и номер документа *</label>
      <select name="col_passport" required>{opts}</select>

      <div class="optional-row">
        <input type="checkbox" id="use_docdt" name="use_docdt" value="1">
        <label for="use_docdt" style="margin-bottom:0">Указать дату выдачи документа</label>
      </div>
      <select name="col_docdt" id="sel_docdt" disabled>{opts}</select>

      <hr style="border:none; border-top:1px solid #eee; margin: 8px 0 16px;">

      <label>Колонка для записи ИНН</label>
      <div class="optional-row">
        <input type="checkbox" id="use_new_inn_col" name="use_new_inn_col" value="1" checked>
        <label for="use_new_inn_col" style="margin-bottom:0">Добавить новую колонку &laquo;ИНН&raquo;</label>
      </div>
      <select name="col_inn_target" id="sel_inn_target" disabled>{opts}</select>

      <hr style="border:none; border-top:1px solid #eee; margin: 8px 0 16px;">

      <label>Название итогового файла</label>
      <input type="text" name="output_name" id="output_name" value="{default_output}">

      <button type="submit">Начать парсинг</button>
    </form>
  </div>
</div>
<script>
const cb = document.getElementById('use_docdt');
const sel = document.getElementById('sel_docdt');
cb.addEventListener('change', () => {{ sel.disabled = !cb.checked; }});

// INN column target toggle
const cbInn = document.getElementById('use_new_inn_col');
const selInn = document.getElementById('sel_inn_target');
cbInn.addEventListener('change', () => {{ selInn.disabled = cbInn.checked; }});

// Auto-select columns by common names
const selects = {{
  col_fio: ['фио', 'fio', 'ф.и.о', 'ф.и.о.'],
  col_bdate: ['дата рождения', 'дата_рождения', 'birthdate', 'bdate', 'др'],
  col_passport: ['паспорт', 'серия номер', 'серия и номер', 'серия_номер', 'passport', 'документ',
                  'серия номер паспорта'],
  col_docdt: ['дата выдачи', 'дата выдачи паспорта', 'дата_выдачи', 'docdt', 'дата выдачи документа'],
}};
const columns = {json.dumps(columns, ensure_ascii=False)};
const lower = columns.map(c => c.toLowerCase().trim());

for (const [selName, hints] of Object.entries(selects)) {{
  const el = document.querySelector('select[name="' + selName + '"]');
  for (const hint of hints) {{
    const idx = lower.indexOf(hint);
    if (idx !== -1) {{
      el.value = columns[idx];
      if (selName === 'col_docdt') {{
        cb.checked = true;
        sel.disabled = false;
      }}
      break;
    }}
  }}
}}
</script>
<div class="footer">prod. by @fourapm</div>
</body></html>""")


def page_progress(task_id: str):
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Обработка — ИНН Парсер</title>{STYLE}</head><body>
<div class="container">
  <h1>Обработка</h1>
  <p class="subtitle" id="statusText">Запуск...</p>
  <div class="card">
    <div class="progress-bar"><div class="fill" id="bar" style="width:0%"></div></div>
    <div class="stats">
      <div class="stat"><div class="num" id="sOk">0</div><div class="lbl">Найдено</div></div>
      <div class="stat"><div class="num" id="sSkip">0</div><div class="lbl">Пропущено</div></div>
      <div class="stat"><div class="num" id="sErr">0</div><div class="lbl">Ошибок</div></div>
      <div class="stat"><div class="num" id="sTotal">0</div><div class="lbl">Всего</div></div>
    </div>

    <div class="captcha-alert" id="captchaAlert">
      <p>Обнаружена капча! Решите её в окне браузера Playwright, затем нажмите кнопку ниже.</p>
      <button onclick="captchaDone()" class="btn-success">Капча решена, продолжить</button>
    </div>

    <div id="stopArea" style="margin: 16px 0;">
      <button id="stopBtn" class="btn-danger" onclick="stopProcessing()">Закончить</button>
    </div>

    <div class="actions" id="actionsArea" style="display:none">
      <a id="downloadLink" class="btn btn-success" href="#">Скачать результат</a>
      <a class="btn" href="/">Загрузить другой файл</a>
    </div>

    <div class="log" id="log"></div>
  </div>
</div>
<script>
const taskId = "{task_id}";
let okCount = 0, skipCount = 0, errCount = 0, totalRows = 0, processed = 0;

const es = new EventSource("/progress/" + taskId);

es.addEventListener("progress", (e) => {{
  const d = JSON.parse(e.data);
  totalRows = d.total;
  document.getElementById('sTotal').textContent = totalRows;

  const status = d.status;
  const fio = d.fio || "";
  const inn = d.inn || "";
  const idx1 = d.idx + 1;

  let cls = "processing";
  let label = "...";

  if (status === "ok") {{
    okCount++; cls = "ok"; label = "OK";
    processed++;
  }} else if (status === "skip" || status === "not_found") {{
    skipCount++; cls = "skip"; label = status === "skip" ? "SKIP" : "НЕ НАЙДЕН";
    processed++;
  }} else if (status === "error") {{
    errCount++; cls = "err"; label = "ОШИБКА";
    processed++;
  }} else if (status === "captcha") {{
    cls = "captcha"; label = "КАПЧА";
    document.getElementById('captchaAlert').style.display = "block";
  }} else if (status === "processing") {{
    cls = "processing"; label = "...";
  }}

  document.getElementById('sOk').textContent = okCount;
  document.getElementById('sSkip').textContent = skipCount;
  document.getElementById('sErr').textContent = errCount;

  if (totalRows > 0) {{
    const pct = Math.round((processed / totalRows) * 100);
    document.getElementById('bar').style.width = pct + "%";
    document.getElementById('statusText').textContent = "Обработано " + processed + " из " + totalRows;
  }}

  const line = document.createElement('div');
  line.className = cls;
  line.textContent = "[" + idx1 + "/" + totalRows + "] " + label + "  " + fio + "  " + inn;
  const log = document.getElementById('log');
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}});

es.addEventListener("done", (e) => {{
  const d = JSON.parse(e.data);
  es.close();
  document.getElementById('statusText').textContent = "Готово!";
  document.getElementById('bar').style.width = "100%";
  document.getElementById('captchaAlert').style.display = "none";
  document.getElementById('stopArea').style.display = "none";
  const a = document.getElementById('actionsArea');
  a.style.display = "flex";
  document.getElementById('downloadLink').href = "/download/" + d.result_file;
}});

es.addEventListener("error_msg", (e) => {{
  const d = JSON.parse(e.data);
  es.close();
  document.getElementById('statusText').textContent = "Ошибка: " + d.message;
}});

function captchaDone() {{
  fetch("/captcha-done/" + taskId, {{ method: "POST" }});
  document.getElementById('captchaAlert').style.display = "none";
}}

function stopProcessing() {{
  fetch("/stop/" + taskId, {{ method: "POST" }}).then(r => r.json()).then(d => {{
    document.getElementById('stopBtn').disabled = true;
    document.getElementById('stopBtn').textContent = "Останавливается...";
  }});
}}
</script>
<div class="footer">prod. by @fourapm</div>
</body></html>""")


# ── Route handlers ───────────────────────────────────────────────

async def handle_index(request: Request):
    return page_upload()


async def handle_upload(request: Request):
    form = await request.form()
    uploaded = form["file"]
    filename = uploaded.filename
    content = await uploaded.read()

    safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    filepath = os.path.join(UPLOAD_DIR, safe_name)
    with open(filepath, "wb") as f:
        f.write(content)

    return RedirectResponse(f"/map/{safe_name}", status_code=303)


async def handle_map(request: Request):
    filename = request.path_params["filename"]
    filepath = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(filepath):
        return HTMLResponse("Файл не найден", status_code=404)

    df = read_file(filepath)
    columns = [c.strip() for c in df.columns.tolist()]
    return page_mapping(filename, columns)


async def handle_start(request: Request):
    form = await request.form()
    filename = form["filename"]
    col_fio = form["col_fio"]
    col_bdate = form["col_bdate"]
    col_passport = form["col_passport"]
    use_docdt = form.get("use_docdt", "")
    col_docdt = form.get("col_docdt", "") if use_docdt else ""

    # INN column target
    use_new_inn_col = form.get("use_new_inn_col", "")
    col_inn = "ИНН" if use_new_inn_col else form.get("col_inn_target", "ИНН")

    # Output filename
    output_name = form.get("output_name", "").strip()
    if not output_name:
        base, _ext = os.path.splitext(filename)
        output_name = f"{base}_with_inn"
    # Ensure .xlsx extension
    if not output_name.lower().endswith(".xlsx"):
        output_name += ".xlsx"

    filepath = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(filepath):
        return HTMLResponse("Файл не найден", status_code=404)

    task_id = uuid.uuid4().hex[:12]
    captcha_event = asyncio.Event()
    stop_event = asyncio.Event()

    tasks[task_id] = {
        "events": [],
        "captcha_event": captcha_event,
        "stop_event": stop_event,
        "done": False,
        "result_file": "",
    }

    # Build result filename
    result_name = output_name
    result_path = os.path.join(RESULT_DIR, result_name)

    df = read_file(filepath)
    df.columns = [c.strip() for c in df.columns]

    async def on_progress(idx, total, status, fio, inn):
        tasks[task_id]["events"].append({
            "type": "progress",
            "data": {"idx": idx, "total": total, "status": status, "fio": fio, "inn": inn}
        })

    async def on_captcha():
        captcha_event.clear()
        await captcha_event.wait()

    async def run_task():
        try:
            result = await process_dataframe(
                df, result_path, col_fio, col_bdate, col_passport, col_docdt,
                col_inn=col_inn,
                on_progress=on_progress,
                on_captcha=on_captcha,
                stop_event=stop_event,
            )
            tasks[task_id]["events"].append({
                "type": "done",
                "data": {"result_file": result_name, **result}
            })
        except Exception as e:
            tasks[task_id]["events"].append({
                "type": "error_msg",
                "data": {"message": str(e)}
            })
        finally:
            tasks[task_id]["done"] = True
            tasks[task_id]["result_file"] = result_name

    asyncio.create_task(run_task())

    return RedirectResponse(f"/progress-page/{task_id}", status_code=303)


async def handle_progress_page(request: Request):
    task_id = request.path_params["task_id"]
    if task_id not in tasks:
        return HTMLResponse("Задача не найдена", status_code=404)
    return page_progress(task_id)


async def handle_progress_sse(request: Request):
    task_id = request.path_params["task_id"]
    if task_id not in tasks:
        return JSONResponse({"error": "not found"}, status_code=404)

    async def event_generator():
        sent = 0
        while True:
            task = tasks.get(task_id)
            if not task:
                break

            while sent < len(task["events"]):
                ev = task["events"][sent]
                sent += 1
                yield {
                    "event": ev["type"],
                    "data": json.dumps(ev["data"], ensure_ascii=False),
                }

            if task["done"] and sent >= len(task["events"]):
                break

            await asyncio.sleep(0.3)

    return EventSourceResponse(event_generator())


async def handle_stop(request: Request):
    task_id = request.path_params["task_id"]
    task = tasks.get(task_id)
    if task:
        task["stop_event"].set()
    return JSONResponse({"ok": True})


async def handle_captcha_done(request: Request):
    task_id = request.path_params["task_id"]
    task = tasks.get(task_id)
    if task:
        task["captcha_event"].set()
    return JSONResponse({"ok": True})


async def handle_download(request: Request):
    filename = request.path_params["filename"]
    filepath = os.path.join(RESULT_DIR, filename)
    if not os.path.exists(filepath):
        return HTMLResponse("Файл не найден", status_code=404)
    return FileResponse(filepath, filename=filename, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── App ──────────────────────────────────────────────────────────

app = Starlette(
    routes=[
        Route("/", handle_index),
        Route("/upload", handle_upload, methods=["POST"]),
        Route("/map/{filename}", handle_map),
        Route("/start", handle_start, methods=["POST"]),
        Route("/progress-page/{task_id}", handle_progress_page),
        Route("/progress/{task_id}", handle_progress_sse),
        Route("/stop/{task_id}", handle_stop, methods=["POST"]),
        Route("/captcha-done/{task_id}", handle_captcha_done, methods=["POST"]),
        Route("/download/{filename}", handle_download),
    ],
)


if __name__ == "__main__":
    print("ИНН Парсер запущен: http://localhost:1337")
    uvicorn.run(app, host="0.0.0.0", port=1337)
