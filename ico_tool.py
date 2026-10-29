#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ico_tool.py —— Windows 图标提取与优化（单文件工具，无第三方依赖）。

模式由源文件类型自动判断：
  * .exe/.dll/.ocx/.cpl/.sys/.mui/.scr  -> PE 模式（从资源节提取图标组）
  * .ico                               -> ICO 模式（查看 / 补齐尺寸）

常用命令：
  python ico_tool.py app.exe                 # 提取第 1 组图标 -> app.ico
  python ico_tool.py app.exe --list          # 列出所有组及每组尺寸
  python ico_tool.py app.exe --all           # 导出所有组（每组一个 .ico 文件）
  python ico_tool.py app.exe --index 2       # 提取第 3 组
  python ico_tool.py app.exe --fill          # 提取第 1 组并补齐推荐 10 档
  python ico_tool.py x.ico --fill 20,40      # 给已有 ico 补指定尺寸（<=256 任意）
  python ico_tool.py x.ico --fill --force    # 用母版重采样覆盖已存在尺寸（提质）
  python ico_tool.py x.ico --list            # 查看 ico 内含尺寸
"""
import argparse
import os
import struct
import sys
import zlib
import ctypes
from ctypes import wintypes

# ============================================================ Windows 资源提取
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
LOAD_LIBRARY_AS_DATAFILE = 0x00000002
RT_GROUP_ICON = 14
RT_ICON = 3
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _setup_protos():
    kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel32.LoadLibraryExW.restype = wintypes.HMODULE
    kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]
    kernel32.FreeLibrary.restype = wintypes.BOOL
    kernel32.FindResourceW.argtypes = [wintypes.HMODULE, wintypes.LPCWSTR, wintypes.LPCWSTR]
    kernel32.FindResourceW.restype = wintypes.HRSRC
    kernel32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    kernel32.SizeofResource.restype = wintypes.DWORD
    kernel32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]
    kernel32.LoadResource.restype = wintypes.HGLOBAL
    kernel32.LockResource.argtypes = [wintypes.HGLOBAL]
    kernel32.LockResource.restype = wintypes.LPVOID
    kernel32.EnumResourceNamesW.argtypes = [wintypes.HMODULE, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPARAM]
    kernel32.EnumResourceNamesW.restype = wintypes.BOOL


def _make_int_resource(i):
    return ctypes.cast(ctypes.c_void_p(i), wintypes.LPCWSTR)


def load_library(path):
    h = kernel32.LoadLibraryExW(path, 0, LOAD_LIBRARY_AS_DATAFILE)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())
    return h


def enum_group_icons(hmod):
    names = []
    Proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, wintypes.LPARAM, wintypes.LPARAM, wintypes.LPARAM)

    def _cb(hModule, lpszType, lpszName, lParam):
        if lpszName and (lpszName & 0xFFFF0000) == 0:
            names.append(lpszName & 0xFFFF)
        else:
            try:
                names.append(ctypes.cast(lpszName, ctypes.c_wchar_p).value)
            except Exception:
                pass
        return True

    cb = Proto(_cb)
    if not kernel32.EnumResourceNamesW(hmod, _make_int_resource(RT_GROUP_ICON), cb, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return names


def get_resource_bytes(hmod, res_type, name):
    name_p = _make_int_resource(name) if isinstance(name, int) else ctypes.c_wchar_p(name)
    type_p = _make_int_resource(res_type) if isinstance(res_type, int) else ctypes.c_wchar_p(res_type)
    hrsrc = kernel32.FindResourceW(hmod, name_p, type_p)
    if not hrsrc:
        raise ctypes.WinError(ctypes.get_last_error())
    size = kernel32.SizeofResource(hmod, hrsrc)
    hglob = kernel32.LoadResource(hmod, hrsrc)
    if not hglob:
        raise ctypes.WinError(ctypes.get_last_error())
    ptr = kernel32.LockResource(hglob)
    if not ptr:
        raise ctypes.WinError(ctypes.get_last_error())
    return ctypes.string_at(ptr, size)


def group_info(hmod, name):
    """返回 (图像数, [(w,h,bpp), ...])"""
    grp = get_resource_bytes(hmod, RT_GROUP_ICON, name)
    reserved, itype, count = struct.unpack_from("<HHH", grp, 0)
    dims = []
    off = 6
    for _ in range(count):
        bWidth, bHeight, bColorCount, bReserved, wPlanes, wBitCount, dwBytesInRes, nID = \
            struct.unpack_from("<BBBBHHIH", grp, off)
        off += 14
        dims.append(((bWidth or 256), (bHeight or 256), wBitCount))
    return count, dims


def build_ico_from_group(hmod, name):
    grp = get_resource_bytes(hmod, RT_GROUP_ICON, name)
    reserved, itype, count = struct.unpack_from("<HHH", grp, 0)
    entries = []
    off = 6
    for _ in range(count):
        bWidth, bHeight, bColorCount, bReserved, wPlanes, wBitCount, dwBytesInRes, nID = \
            struct.unpack_from("<BBBBHHIH", grp, off)
        off += 14
        img = get_resource_bytes(hmod, RT_ICON, nID)
        entries.append((bWidth, bHeight, bColorCount, bReserved, wPlanes, wBitCount, len(img), img))
    icon_dir = struct.pack("<HHH", 0, 1, count)
    icon_entries = b""
    image_data = b""
    cursor = 6 + 16 * count
    for (bWidth, bHeight, bColorCount, bReserved, wPlanes, wBitCount, img_len, img) in entries:
        icon_entries += struct.pack("<BBBBHHII", bWidth, bHeight, bColorCount, bReserved,
                                    wPlanes, wBitCount, img_len, cursor)
        image_data += img
        cursor += img_len
    return icon_dir + icon_entries + image_data


# ================================================================ ICO 解析/拼装
def ico_images(data):
    """返回 ico 内每个图像(ICONIMAGE)的原始字节列表。"""
    count = struct.unpack_from("<H", data, 4)[0]
    imgs = []
    off = 6
    for _ in range(count):
        bw, bh, bc, br, wp, wb, bl, o = struct.unpack_from("<BBBBHHII", data, off)
        imgs.append(data[o:o + bl])
        off += 16
    return imgs


def image_dims(img):
    """返回 (bw_field, bh_field, bpp, bColorCount) 用于写入 ICONDIRENTRY。"""
    if img[:8] == PNG_SIG:
        w, h = struct.unpack_from(">II", img, 16)
        ctype = img[25]
        bpp = {6: 32, 4: 32, 2: 24, 3: 8, 0: 8}.get(ctype, 32)
        bw = 0 if w == 256 else w
        bh = 0 if h == 256 else h
        return bw, bh, bpp, 0
    (bisize, biwidth, biheight, biplanes, bibitcount, comp,
     szimg, xppm, yppm, clrused, clrimp) = struct.unpack_from("<IiiHHIIiiII", img, 0)
    w = biwidth
    h = biheight // 2
    bw = 0 if w == 256 else w
    bh = 0 if h == 256 else h
    bc = 0 if bibitcount >= 8 else (1 << bibitcount)
    return bw, bh, bibitcount, bc


def assemble_ico(images):
    entries = [image_dims(im) + (im,) for im in images]
    icon_dir = struct.pack("<HHH", 0, 1, len(entries))
    eb = b""
    data = b""
    cur = 6 + 16 * len(entries)
    for (bw, bh, bpp, bc, im) in entries:
        eb += struct.pack("<BBBBHHII", bw, bh, bc, 0, 1, bpp, len(im), cur)
        data += im
        cur += len(im)
    return icon_dir + eb + data


# ================================================================== PNG/DIB 解码
def decode_png(data):
    if data[:8] != PNG_SIG:
        raise ValueError("not PNG")
    pos = 8
    width = height = bit_depth = color_type = None
    idat = b""
    while pos < len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        ctype = data[pos + 4:pos + 8]
        cdata = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack_from(">IIBB", cdata, 0)
        elif ctype == b"IDAT":
            idat += cdata
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if bit_depth != 8:
        raise ValueError("only 8-bit PNG (got %d)" % bit_depth)
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    raw = zlib.decompress(idat)
    stride = width * ch
    out = bytearray(height * stride)
    prev = bytes(stride)
    p = 0
    for y in range(height):
        ftype = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        if ftype == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = bytes(line)
    if color_type == 6:
        rgba = out
    elif color_type == 2:
        rgba = bytearray(height * width * 4)
        for i in range(height * width):
            rgba[i * 4:i * 4 + 3] = out[i * 3:i * 3 + 3]
            rgba[i * 4 + 3] = 255
    elif color_type == 4:
        rgba = bytearray(height * width * 4)
        for i in range(height * width):
            g = out[i * 2]; a = out[i * 2 + 1]
            rgba[i * 4:i * 4 + 4] = (g, g, g, a)
    elif color_type == 0:
        rgba = bytearray(height * width * 4)
        for i in range(height * width):
            g = out[i]
            rgba[i * 4:i * 4 + 4] = (g, g, g, 255)
    else:
        raise ValueError("unsupported PNG color type %d" % color_type)
    return width, height, rgba


def decode_dib_ico(img):
    if len(img) < 40:
        return None
    (bisize, biwidth, biheight, biplanes, bibitcount, comp,
     szimg, xppm, yppm, clrused, clrimp) = struct.unpack_from("<IiiHHIIiiII", img, 0)
    if bisize < 40 or biwidth <= 0 or biheight <= 0 or comp != 0:
        return None
    if bibitcount not in (1, 4, 8, 24, 32):
        return None
    h = biheight // 2
    w = biwidth
    if bibitcount <= 8:
        clr_used = clrused if clrused != 0 else (1 << bibitcount)
    else:
        clr_used = 0
    palette = []
    p = 40
    for _ in range(clr_used):
        b, g, r, _ = img[p], img[p + 1], img[p + 2], img[p + 3]
        palette.append((r, g, b))
        p += 4
    xor_off = p
    xor_row = ((w * bibitcount + 31) // 32) * 4
    and_row = ((w + 31) // 32) * 4
    and_off = xor_off + xor_row * h
    if and_off + and_row * h > len(img):
        return None
    rgba = bytearray(w * h * 4)
    for y in range(h):
        xr = xor_off + (h - 1 - y) * xor_row
        ar = and_off + (h - 1 - y) * and_row
        for x in range(w):
            andbit = (img[ar + x // 8] >> (7 - (x & 7))) & 1
            if bibitcount == 32:
                base = xr + x * 4
                b, g, r, a = img[base], img[base + 1], img[base + 2], img[base + 3]
                A = 0 if andbit else (a if a != 0 else 255)
            elif bibitcount == 24:
                base = xr + x * 3
                b, g, r = img[base], img[base + 1], img[base + 2]
                A = 0 if andbit else 255
            else:
                if bibitcount == 8:
                    idx = img[xr + x]
                elif bibitcount == 4:
                    byte = img[xr + x // 2]
                    idx = (byte >> 4) & 0xF if x % 2 == 0 else byte & 0xF
                else:
                    byte = img[xr + x // 8]
                    idx = (byte >> (7 - (x & 7))) & 1
                r, g, b = palette[idx]
                A = 0 if andbit else 255
            o = (y * w + x) * 4
            rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3] = r, g, b, A
    return w, h, rgba


def decode_ico_image(img):
    if img[:8] == PNG_SIG:
        return decode_png(img)
    return decode_dib_ico(img)


# ================================================================== 缩放/编码
def resize_rgba(sw, sh, src, dw, dh):
    dst = bytearray(dw * dh * 4)
    for dy in range(dh):
        y0 = dy * sh // dh
        y1 = max(y0 + 1, (dy + 1) * sh // dh)
        for dx in range(dw):
            x0 = dx * sw // dw
            x1 = max(x0 + 1, (dx + 1) * sw // dw)
            sR = sG = sB = sA = 0
            n = 0
            for sy in range(y0, y1):
                base = sy * sw * 4
                for sx in range(x0, x1):
                    o = base + sx * 4
                    r = src[o]; g = src[o + 1]; b = src[o + 2]; a = src[o + 3]
                    sR += r * a; sG += g * a; sB += b * a; sA += a
                    n += 1
            o2 = (dy * dw + dx) * 4
            if sA == 0:
                dst[o2:o2 + 4] = (0, 0, 0, 0)
            else:
                dst[o2] = min(255, sR // sA)
                dst[o2 + 1] = min(255, sG // sA)
                dst[o2 + 2] = min(255, sB // sA)
                dst[o2 + 3] = sA // n
    return dst


def encode_icoimage_32(w, h, rgba_topdown):
    xor = bytearray()
    for y in range(h - 1, -1, -1):
        row = rgba_topdown[y * w * 4:(y + 1) * w * 4]
        for i in range(0, len(row), 4):
            r, g, b, a = row[i], row[i + 1], row[i + 2], row[i + 3]
            xor += bytes((b, g, r, a))
    xor = bytes(xor)
    row_bytes = (w + 7) // 8
    padded = (row_bytes + 3) & ~3
    and_mask = b"\x00" * (padded * h)
    bmphdr = struct.pack("<IiiHHIIiiII", 40, w, 2 * h, 1, 32, 0, 0, 0, 0, 0, 0)
    return bmphdr + xor + and_mask


STANDARD = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def parse_sizes(s):
    if s is None or str(s).lower() in ("recommended", "all", "standard", "true", ""):
        return list(STANDARD)
    out = []
    for tok in str(s).split(","):
        tok = tok.strip()
        if tok:
            v = int(tok)
            if v <= 0 or v > 256:
                raise ValueError(
                    "ICO 格式最大仅支持 256x256（%s 超出范围）；>256 的尺寸(如512)无法写入 .ico，"
                    "请改用 PNG / icns 等其他格式" % tok)
            out.append(v)
    return sorted(set(out))


def optimize_ico(images, sizes, force=False):
    decoded = [decode_ico_image(im) for im in images]
    valid = [d for d in decoded if d]
    if not valid:
        raise ValueError("没有任何可解码的源图像，无法补齐尺寸")
    existing = {(w, h) for (w, h, _) in valid}
    master = max(valid, key=lambda t: t[0] * t[1])
    mw, mh, mrgba = master
    target = set(sizes)
    # 保留原图；若 --force，则丢弃目标尺寸的原图（稍后由母版重采样覆盖）
    out = []
    for im, dec in zip(images, decoded):
        if force and dec and dec[0] in target:
            continue
        out.append(im)
    added, replaced, skipped = [], [], []
    for s in sizes:
        present = (s, s) in existing
        if present and not force:
            skipped.append(s)
            continue
        if s > max(mw, mh):
            sys.stderr.write("警告: 目标尺寸 %d 大于母版(%dx%d)，将放大（可能偏糊）\n" % (s, mw, mh))
        rgba = resize_rgba(mw, mh, mrgba, s, s)
        out.append(encode_icoimage_32(s, s, rgba))
        (replaced if present else added).append(s)
    return assemble_ico(out), added, replaced, skipped, (mw, mh)


# ====================================================================== 主流程
def _default_out(src, suffix):
    stem = os.path.splitext(os.path.basename(src))[0]
    return stem + suffix


def main():
    ap = argparse.ArgumentParser(description="Windows 图标提取与优化（单文件，无依赖）")
    ap.add_argument("src", help="源文件：.exe/.dll（提取）或 .ico（优化）")
    ap.add_argument("out", nargs="?", help="输出 .ico（可选，缺省自动命名）")
    ap.add_argument("--out", dest="out_opt", help="同位置参数：指定输出 .ico 路径")
    ap.add_argument("--list", action="store_true", help="列出图标组 / 尺寸")
    ap.add_argument("--all", action="store_true", help="(PE) 导出所有组，每组一个 .ico 文件")
    ap.add_argument("--index", type=int, default=0, help="(PE) 要提取的组序号，默认 0=第一组")
    ap.add_argument("--fill", nargs="?", const="recommended",
                    help="补齐尺寸：'recommended'(10档) 或逗号列表如 20,40")
    ap.add_argument("--force", action="store_true",
                    help="(配合 --fill) 用母版重采样覆盖已存在的同尺寸图像，提升质量")
    args = ap.parse_args()
    args.out = args.out_opt or args.out

    ext = os.path.splitext(args.src)[1].lower()
    is_ico = ext == ".ico"

    if is_ico:
        _run_ico(args)
    else:
        _run_pe(args)


def _run_ico(args):
    with open(args.src, "rb") as f:
        data = f.read()
    imgs = ico_images(data)
    if args.list or not args.fill:
        dimset = []
        for im in imgs:
            bw, bh, bpp, bc = image_dims(im)
            dimset.append((bw or 256, bh or 256, bpp))
        dims = sorted({(w, h) for (w, h, _) in dimset})
        print("文件: %s" % args.src)
        print("图像总数: %d" % len(imgs))
        print("尺寸档: %s" % (",".join("%dx%d" % (w, h) for w, h in dims) or "（无）"))
        if not args.fill:
            return
    # --fill
    sizes = parse_sizes(args.fill)
    result, added, replaced, skipped, (mw, mh) = optimize_ico(imgs, sizes, args.force)
    out = args.out or _default_out(args.src, "_opt.ico")
    with open(out, "wb") as f:
        f.write(result)
    print("已补齐尺寸 -> %s" % out)
    print("  母版: %dx%d | 新增:%s 覆盖:%s 跳过:%s" % (
        mw, mh, added or "无", replaced or "无", skipped or "无"))


def _run_pe(args):
    _setup_protos()
    hmod = load_library(args.src)
    try:
        names = enum_group_icons(hmod)
        if not names:
            print("错误: %s 中没有 RT_GROUP_ICON 图标资源" % args.src)
            return 1
        if args.list:
            print("文件: %s" % args.src)
            print("图标组总数: %d" % len(names))
            for i, name in enumerate(names):
                cnt, dims = group_info(hmod, name)
                dimset = sorted({(w, h) for (w, h, _) in dims})
                bppset = sorted({b for (_, _, b) in dims})
                print("  [%d] name=%s 图像数=%d 尺寸=%s bpp=%s" % (
                    i, name, cnt,
                    ",".join(str(w) for w, _ in dimset), bppset))
            return 0
        if args.index < 0 or args.index >= len(names):
            print("错误: --index %d 越界（共 %d 组，可用 0..%d）" % (
                args.index, len(names), len(names) - 1))
            return 1
        ico = build_ico_from_group(hmod, names[args.index])
        if args.all:
            for i, name in enumerate(names):
                g = build_ico_from_group(hmod, name)
                out = _default_out(args.src, "_group%d.ico" % i)
                with open(out, "wb") as f:
                    f.write(g)
                print("已导出 %s" % out)
            return 0
        if args.fill:
            sizes = parse_sizes(args.fill)
            result, added, replaced, skipped, (mw, mh) = optimize_ico(ico_images(ico), sizes, args.force)
            out = args.out or _default_out(args.src, ".ico")
            with open(out, "wb") as f:
                f.write(result)
            print("已提取第 %d 组并补齐尺寸 -> %s" % (args.index, out))
            print("  母版: %dx%d | 新增:%s 覆盖:%s 跳过:%s" % (
                mw, mh, added or "无", replaced or "无", skipped or "无"))
            return 0
        # 默认：仅提取第 1 组
        out = args.out or _default_out(args.src, ".ico")
        with open(out, "wb") as f:
            f.write(ico)
        print("已提取第 %d 组图标 -> %s (%d 字节)" % (args.index, out, len(ico)))
        return 0
    finally:
        kernel32.FreeLibrary(hmod)


if __name__ == "__main__":
    sys.exit(main() or 0)
