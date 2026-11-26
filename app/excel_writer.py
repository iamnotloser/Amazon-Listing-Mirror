"""按「竞品LISTING分析模板」生成 Excel：横向多列、图片嵌入、6 列自动拆表。"""
import hashlib
import os
import re
import shutil
import zipfile
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter, coordinate_to_tuple
from openpyxl.utils.units import pixels_to_EMU
from PIL import Image as PILImage

PER_SHEET = 6

# 图片呈现方式：
#   float = 单元格大小的浮动图（默认，WPS / 微软 Excel 都能看到同一张图，视觉上等同嵌入）
#   cell  = 只写 DISPIMG（真嵌入单元格，仅 WPS 可显示）
#   both  = 两者叠加（不推荐，会出现两张图）
IMAGE_MODE = (os.getenv("IMAGE_MODE") or "float").strip().lower()
LABEL_FILL = PatternFill("solid", fgColor="DEE0E3")
WAIT_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="B7BCC2")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
F_LABEL = Font(name="宋体", size=12, bold=True)
F_SUB = Font(name="宋体", size=11, bold=True)
F_DATA = Font(name="宋体", size=10.5)
F_SMALL = Font(name="宋体", size=9)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
TOPLEFT = Alignment(horizontal="left", vertical="top", wrap_text=True)

A_PLUS_START = 24


def _money(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    s = str(value).replace(" ", "")
    keep = "".join(ch for ch in s if ch.isdigit() or ch in ".,")
    if not keep:
        return None
    if "," in keep and "." in keep:
        keep = keep.replace(",", "")
    elif "," in keep:
        keep = keep.replace(",", ".") if re.search(r",\d{2}$", keep) else keep.replace(",", "")
    try:
        return round(float(keep), 2)
    except ValueError:
        return None


def _rating(text):
    m = re.search(r"(\d+[.,]?\d*)", text or "")
    return m.group(1).replace(",", ".") if m else None


def _thumb(path, max_w=700):
    """Excel 里只需要缩略图；原图仍保留在 images 目录，避免工作簿过大。"""
    if not path or not os.path.exists(path):
        return None
    folder = os.path.join(os.path.dirname(path), "_thumb")
    out = os.path.join(folder, os.path.splitext(os.path.basename(path))[0] + ".jpg")
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(path):
        return out
    try:
        os.makedirs(folder, exist_ok=True)
        with PILImage.open(path) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w > max_w:
                im = im.resize((max_w, int(h * max_w / w)), PILImage.LANCZOS)
            im.save(out, "JPEG", quality=86, optimize=True)
        return out
    except Exception:
        return path


def _cell_img(ws, path, cell, registry):
    """WPS 嵌入单元格图片：单元格写 DISPIMG 公式，图片挂在 cellimages 部件里。"""
    path = _thumb(path)
    if not path or not os.path.exists(path):
        return False
    key = hashlib.md5(
        (os.path.abspath(path) + str(os.path.getmtime(path))).encode()
    ).hexdigest().upper()
    img_id = f"ID_{key}"
    registry[img_id] = path
    ws[cell].value = f'=_xlfn.DISPIMG("{img_id}",1)'
    return True


def _float_img(ws, path, cell):
    """同表浮动图兜底：Excel 不认 DISPIMG，用锚定图片盖住单元格。"""
    path = _thumb(path)
    if not path or not os.path.exists(path):
        return False
    row, col = coordinate_to_tuple(cell)
    height_pt = ws.row_dimensions[row].height or 15
    width_ch = ws.column_dimensions[get_column_letter(col)].width or 8.43
    box_w = int(width_ch * 7 + 5) - 6
    box_h = int(height_pt * 4 / 3) - 6
    try:
        with PILImage.open(path) as im:
            w, h = im.size
        scale = min(box_w / w, box_h / h, 1)  # 只缩小不放大
        px_w, px_h = max(1, int(w * scale)), max(1, int(h * scale))
        img = XLImage(path)
        img.width, img.height = px_w, px_h
        marker = AnchorMarker(
            col=col - 1, colOff=pixels_to_EMU(max(0, (box_w - px_w) // 2)),
            row=row - 1, rowOff=pixels_to_EMU(max(0, (box_h - px_h) // 2)),
        )
        img.anchor = OneCellAnchor(
            _from=marker, ext=XDRPositiveSize2D(pixels_to_EMU(px_w), pixels_to_EMU(px_h))
        )
        ws.add_image(img)
        return True
    except Exception:
        return False


def _put_img(ws, path, cell, registry, mode):
    """按模式写图片；返回是否成功。"""
    if not path:
        return False
    ok = False
    if mode in ("cell", "both"):
        ok = _cell_img(ws, path, cell, registry)
    if mode in ("float", "both"):
        ok = _float_img(ws, path, cell) or ok
    return ok


def _set(ws, cell, value, font=F_DATA, align=CENTER, fill=None, fmt=None):
    c = ws[cell]
    c.value = value
    c.font = font
    c.alignment = align
    c.border = BORDER
    if fill:
        c.fill = fill
    if fmt:
        c.number_format = fmt
    return c


def _label_block(ws, r1, r2, text):
    ws.merge_cells(start_row=r1, start_column=1, end_row=r2, end_column=1)
    c = ws.cell(row=r1, column=1, value=text)
    c.font = F_LABEL
    c.alignment = CENTER
    c.fill = LABEL_FILL
    for r in range(r1, r2 + 1):
        ws.cell(row=r, column=1).border = BORDER
        ws.cell(row=r, column=1).fill = LABEL_FILL
        ws.cell(row=r, column=2).border = BORDER


def _parse_date(text):
    if not text:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return text


def _sheet(ws, items, meta, registry, mode=IMAGE_MODE):
    """items: 本表内的竞品数据（含 local_images 字段）。"""
    n = len(items)
    aplus_n = max([5] + [len(i.get("aplus_local") or []) for i in items])
    title_row = A_PLUS_START + aplus_n + 1
    highlight_row = title_row + 1
    bullet_row = highlight_row + 1
    note_row = bullet_row + 5

    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 9.5
    for idx in range(n):
        ws.column_dimensions[get_column_letter(3 + idx)].width = 34
    ws.freeze_panes = "C1"
    ws.sheet_view.showGridLines = False

    heights = {1: 150, 2: 24, 3: 24, 4: 34, 5: 26, 6: 36, 7: 26, 8: 26, 9: 26, 10: 26,
               11: 26, 12: 26, 13: 26, 14: 150, 15: 60, 16: 60}
    for r in range(17, 24):
        heights[r] = 130
    for r in range(A_PLUS_START, A_PLUS_START + aplus_n):
        heights[r] = 130
    heights[title_row] = 90
    heights[highlight_row] = 70
    for r in range(bullet_row, bullet_row + 5):
        heights[r] = 110
    heights[note_row] = 60
    for r, h in heights.items():
        ws.row_dimensions[r].height = h

    # ---- 左侧标签区 ----
    _label_block(ws, 1, 1, "竞争对手产品图片")
    _label_block(ws, 2, 2, "对手品牌-简称")
    _label_block(ws, 3, 3, "Asin")
    _label_block(ws, 4, 4, "对手选择原因简述")
    _label_block(ws, 5, 5, "开售日")
    _label_block(ws, 6, 10, "促销设置")
    for r, t in zip(range(6, 11), ["近30天销量", "最终到手价", "划线价", "标价", "折扣"]):
        _set(ws, f"B{r}", t, font=F_SUB, fill=LABEL_FILL)
    _label_block(ws, 11, 13, "标记与\n评分情况")
    for r, t in zip(range(11, 14), ["AC标", "绿标", "评分水平"]):
        _set(ws, f"B{r}", t, font=F_SUB, fill=LABEL_FILL)
    _label_block(ws, 14, 16, "变体\n&\n配件\n&\n捆绑")
    _set(ws, "B14", "变体情况  截图", font=F_SUB, fill=LABEL_FILL)
    _set(ws, "B15", "变体情况  补充说明", font=F_SUB, fill=LABEL_FILL)
    _set(ws, "B16", "捆绑/配件情况   补充说明", font=F_SUB, fill=LABEL_FILL)
    _label_block(ws, 17, 23, "主图")
    for r in range(17, 24):
        _set(ws, f"B{r}", f"{r - 16}-图", font=F_SUB, fill=LABEL_FILL)
    _label_block(ws, A_PLUS_START, A_PLUS_START + aplus_n - 1, "A+图")
    for i in range(1, aplus_n + 1):
        _set(ws, f"B{A_PLUS_START + i - 1}", f"{i}-A+", font=F_SUB, fill=LABEL_FILL)
    _label_block(ws, title_row, highlight_row, "详情页\n文案")
    _set(ws, f"B{title_row}", "商品主标题", font=F_SUB, fill=LABEL_FILL)
    _set(ws, f"B{highlight_row}", "商品亮点", font=F_SUB, fill=LABEL_FILL)
    ws.merge_cells(start_row=bullet_row, start_column=1, end_row=bullet_row + 4, end_column=1)
    c = ws.cell(row=bullet_row, column=1, value="对手五行")
    c.font = F_LABEL
    c.alignment = CENTER
    c.fill = LABEL_FILL
    for r in range(bullet_row, bullet_row + 5):
        ws.cell(row=r, column=1).fill = LABEL_FILL
        ws.cell(row=r, column=1).border = BORDER
        ws.cell(row=r, column=2).border = BORDER
    note_cell = ws.cell(row=note_row, column=1, value="分析备注")
    note_cell.font = F_LABEL
    note_cell.fill = LABEL_FILL
    note_cell.alignment = CENTER
    note_cell.border = BORDER
    ws.merge_cells(start_row=note_row, start_column=2, end_row=note_row, end_column=2 + n)
    ws.cell(row=note_row, column=2).alignment = TOPLEFT
    ws.cell(row=note_row, column=2).font = F_SMALL
    ws.cell(row=note_row, column=2).value = meta.get("note") or ""

    # ---- 每个 ASIN 一列 ----
    for i, it in enumerate(items):
        col = 3 + i
        L = get_column_letter(col)

        for r in range(1, note_row):
            ws.cell(row=r, column=col).border = BORDER

        _set(ws, f"{L}1", None)
        _put_img(ws, (it.get("main_local") or [None])[0], f"{L}1", registry, mode)
        _set(ws, f"{L}2", it.get("brand") or "待补充", fill=None if it.get("brand") else WAIT_FILL)
        _set(ws, f"{L}3", it.get("asin"))
        _set(ws, f"{L}4", it.get("reason") or "手动指定", align=TOPLEFT, font=F_SMALL)
        d = _parse_date(it.get("date_first_available"))
        _set(ws, f"{L}5", d if d else "待补充", fill=None if d else WAIT_FILL,
             fmt="yyyy-mm-dd" if isinstance(d, datetime) else None)

        sales = it.get("sales_30d")
        _set(ws, f"{L}6", sales if sales not in (None, "") else "待补充",
             fill=None if sales not in (None, "") else WAIT_FILL)
        _set(ws, f"{L}7", _money(it.get("price")), fmt="#,##0.00")
        _set(ws, f"{L}8", _money(it.get("list_price")), fmt="#,##0.00")
        _set(ws, f"{L}9", _money(it.get("typical_price") or it.get("list_price")), fmt="#,##0.00")
        _set(ws, f"{L}10",
             f'=IF(AND(ISNUMBER({L}7),ISNUMBER({L}8),{L}8>0),1-{L}7/{L}8,"")',
             fmt="0.0%")

        _set(ws, f"{L}11", "有" if it.get("ac_badge") else "无")
        _set(ws, f"{L}12", "有" if it.get("climate_badge") else "无")
        rating = _rating(it.get("rating"))
        reviews = (it.get("review_count") or "").strip("() ")
        _set(ws, f"{L}13", f"{rating}/{reviews}" if rating else "待补充",
             fill=None if rating else WAIT_FILL)

        _put_img(ws, it.get("variation_local"), f"{L}14", registry, mode)
        dims = it.get("variations", {}).get("dimensions") or []
        if dims:
            lines = [f"{x['name']}（{len(x['values'])}）：" + "、".join(x["values"][:12]) for x in dims]
            var_text = "\n".join(lines) + f"\n变体总数：{it.get('variations', {}).get('count', 0)}"
        else:
            var_text = "无变体"
        _set(ws, f"{L}15", var_text, align=TOPLEFT, font=F_SMALL)
        bundle = it.get("bundle_note") or ("；".join(it.get("bundle_hint") or []) or "待补充")
        _set(ws, f"{L}16", bundle, align=TOPLEFT, font=F_SMALL,
             fill=WAIT_FILL if bundle == "待补充" else None)

        for k in range(7):
            p = (it.get("main_local") or [])
            _set(ws, f"{L}{17 + k}", None)
            if k < len(p):
                _put_img(ws, p[k], f"{L}{17 + k}", registry, mode)

        for k in range(aplus_n):
            p = it.get("aplus_local") or []
            if k < len(p):
                _put_img(ws, p[k], f"{L}{A_PLUS_START + k}", registry, mode)
            else:
                if k == 0 and not p:
                    _set(ws, f"{L}{A_PLUS_START}", "无 A+ 内容", fill=WAIT_FILL, font=F_SMALL)
                else:
                    _set(ws, f"{L}{A_PLUS_START + k}", None)

        _set(ws, f"{L}{title_row}", it.get("title_main") or it.get("title") or "待补充",
             align=TOPLEFT, font=F_SMALL)
        highlights = it.get("title_highlights")
        _set(ws, f"{L}{highlight_row}", highlights or "待补充", align=TOPLEFT, font=F_SMALL,
             fill=None if highlights else WAIT_FILL)
        bullets = it.get("bullets") or []
        for k in range(5):
            _set(ws, f"{L}{bullet_row + k}",
                 bullets[k] if k < len(bullets) else None, align=TOPLEFT, font=F_SMALL)

    ws.merge_cells(start_row=note_row, start_column=3, end_row=note_row, end_column=2 + n)
    ws.cell(row=note_row, column=3).border = BORDER

    # 旧标题 / 新标题（人工填写区）
    old_row = note_row + 2
    for label, r in (("旧标题（人工填写）", old_row), ("新标题（人工填写）", old_row + 1)):
        ws.cell(row=r, column=1, value=label).font = F_SUB
        ws.cell(row=r, column=1).fill = LABEL_FILL
        ws.cell(row=r, column=1).alignment = CENTER
        ws.cell(row=r, column=1).border = BORDER
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=2 + n)
        ws.cell(row=r, column=2).alignment = TOPLEFT
        ws.cell(row=r, column=2).border = BORDER
        ws.row_dimensions[r].height = 60


def build_workbook(items, out_path, meta=None, image_mode=None):
    meta = meta or {}
    mode = (image_mode or IMAGE_MODE or "float").strip().lower()
    if mode not in ("cell", "float", "both"):
        mode = "float"
    registry = {}
    wb = Workbook()
    wb.remove(wb.active)
    for start in range(0, len(items), PER_SHEET):
        chunk = items[start:start + PER_SHEET]
        idx = start // PER_SHEET
        name = "链接分析" if idx == 0 else f"链接分析{idx + 1}"
        ws = wb.create_sheet(name)
        _sheet(ws, chunk, {**meta, "note": meta.get("note", "")}, registry, mode)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    try:
        wb.calculation.fullCalcOnLoad = True  # 让 WPS 打开时重算 DISPIMG
    except Exception:
        pass
    wb.save(out_path)
    _inject_cellimages(out_path, registry)
    return out_path


NS_ETC = "http://www.wps.cn/officeDocument/2017/etCustomData"
NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _inject_cellimages(xlsx_path, registry):
    """把图片写入 WPS 的 cellimages 部件，实现「嵌入单元格」效果。

    结构完全对齐模板：
      xl/cellimages.xml + xl/_rels/cellimages.xml.rels
      [Content_Types].xml 增加 cellimage Override
      xl/_rels/workbook.xml.rels 增加 2020/cellImage 关系
    """
    if not registry:
        return
    tmp = xlsx_path + ".tmp"
    with zipfile.ZipFile(xlsx_path) as zin:
        used = {int(m.group(1)) for n in zin.namelist()
                if (m := re.search(r"xl/media/image(\d+)\.", n))}
        next_idx = (max(used) + 1) if used else 1

        pics, rels, medias = [], [], []
        for i, (img_id, path) in enumerate(registry.items(), 1):
            fname = f"image{next_idx}.jpeg"
            next_idx += 1
            with PILImage.open(path) as im:
                w, h = im.size
                if im.format == "JPEG":
                    with open(path, "rb") as f:
                        data = f.read()
                else:  # 统一转 JPEG，避免 png/webp 直接改后缀导致图片损坏
                    buf = BytesIO()
                    im.convert("RGB").save(buf, "JPEG", quality=86, optimize=True)
                    data = buf.getvalue()
            ext = f'<a:ext cx="{int(w * 9525)}" cy="{int(h * 9525)}"/>'
            pics.append(
                '<etc:cellImage><xdr:pic><xdr:nvPicPr>'
                f'<xdr:cNvPr id="{168 + i}" name="{img_id}"/>'
                '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr>'
                f'</xdr:nvPicPr><xdr:blipFill><a:blip r:embed="rId{i}"/>'
                '<a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
                '<xdr:spPr><a:xfrm><a:off x="0" y="0"/>'
                f'{ext}</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                '<a:noFill/><a:ln w="9525"><a:noFill/></a:ln></xdr:spPr>'
                '</xdr:pic></etc:cellImage>'
            )
            rels.append(
                f'<Relationship Id="rId{i}" Type="{NS_R}/image" Target="media/{fname}"/>'
            )
            medias.append((f"xl/media/{fname}", data))

        cellimages = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<etc:cellImages xmlns:xdr="{NS_XDR}" xmlns:r="{NS_R}" '
            f'xmlns:a="{NS_A}" xmlns:etc="{NS_ETC}">' + "".join(pics) + "</etc:cellImages>"
        )
        cellrels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels) + "</Relationships>"
        )

        ct = zin.read("[Content_Types].xml").decode("utf-8")
        if "cellimages.xml" not in ct:
            ct = ct.replace(
                "</Types>",
                '<Override PartName="/xl/cellimages.xml" '
                'ContentType="application/vnd.wps-officedocument.cellimage+xml"/></Types>'
            )
        if 'Extension="jpeg"' not in ct and 'Extension="jpg"' not in ct:
            ct = re.sub(
                r"(<Types[^>]*>)",
                r'\1<Default Extension="jpeg" ContentType="image/jpeg"/>',
                ct, count=1
            )

        wbrels = zin.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        if "cellimages.xml" not in wbrels:
            wbrels = wbrels.replace(
                "</Relationships>",
                '<Relationship Id="rIdCellImages" '
                'Type="http://www.wps.cn/officeDocument/2020/cellImage" '
                'Target="cellimages.xml"/></Relationships>'
            )

        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            zout.writestr("[Content_Types].xml", ct)
            for item in zin.infolist():
                if item.filename in ("[Content_Types].xml", "xl/_rels/workbook.xml.rels"):
                    continue
                zout.writestr(item, zin.read(item.filename))
            zout.writestr("xl/_rels/workbook.xml.rels", wbrels)
            zout.writestr("xl/cellimages.xml", cellimages)
            zout.writestr("xl/_rels/cellimages.xml.rels", cellrels)
            for name, data in medias:
                zout.writestr(name, data)

    shutil.move(tmp, xlsx_path)
