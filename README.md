# ico_tool.py — Windows 图标提取与优化工具

> 单文件、零第三方依赖的命令行工具：从 `.exe` / `.dll` 里提取程序图标，或给已有 `.ico` 补齐多尺寸，生成对 Windows / Win11 友好的图标文件。

*Extract icons from PE files and fill missing sizes into `.ico` — one standalone Python script, no pip install needed.*

---

## 特性

- **提取**：从任意 Windows PE（`.exe` / `.dll` / `.ocx` / `.cpl` / `.sys` / `.mui` / `.scr`）的资源节中取出图标组。
- **补齐尺寸**：以图标内最高分辨率的母版重采样，补出 16 / 20 / 24 / 32 / 40 / 48 / 64 / 96 / 128 / 256 等尺寸，覆盖 Win11 各显示场景。
- **零依赖**：仅用 Python 标准库（`ctypes` + `zlib`），无需联网 `pip install`。
- **纯 Windows 原生格式**：补出的小尺寸用经典 32 位非压缩 DIB 编码（Windows 图标加载器仅对 256×256 可靠支持 PNG，更小的尺寸必须用非压缩格式，否则系统会跳过该档）。
- **安全**：以 `LOAD_LIBRARY_AS_DATAFILE` 方式映射文件，**不执行**目标程序。

---

## 环境要求

- Windows 操作系统（脚本直接调用 Win32 API）。
- Python 3（开发/验证于 3.13，3.8+ 应均可）。
- 不需要安装任何第三方包。

---

## 安装

把 `ico_tool.py` 这一个文件拿过去即可：

```bash
git clone https://github.com/你的用户名/ico_tool.git
cd ico_tool
# 直接用，无需 pip install
```

---

## 用法

脚本根据**源文件类型**自动切换模式：

| 源文件 | 模式 |
|---|---|
| `.exe` / `.dll` / … | 提取模式（读资源节） |
| `.ico` | 优化模式（查看 / 补齐尺寸） |

### 常用命令

```bash
# —— 提取模式（源为 exe / dll）——
python ico_tool.py app.exe                 # 提取第 1 组图标 -> app.ico
python ico_tool.py app.exe --list          # 列出所有图标组及每组尺寸/bpp
python ico_tool.py app.exe --all           # 导出所有组，每组一个文件 app_group0.ico …
python ico_tool.py app.exe --index 2       # 提取第 3 组（组序号从 0 开始）
python ico_tool.py app.exe --fill          # 提取第 1 组并补齐推荐 10 档
python ico_tool.py app.exe --fill --force  # 同上，并用母版重采样覆盖已存在尺寸
python ico_tool.py app.exe --out out.ico   # 指定输出文件名

# —— 优化模式（源为 ico）——
python ico_tool.py icon.ico --list         # 查看该 ico 内含哪些尺寸
python ico_tool.py icon.ico --fill         # 补齐推荐 10 档 -> icon_opt.ico
python ico_tool.py icon.ico --fill 20,40,96,128   # 只补指定尺寸（≤256 任意正整数）
python ico_tool.py icon.ico --fill --force # 用母版重采样覆盖全部目标尺寸
```

---

## 参数说明

| 参数 | 说明 |
|---|---|
| `src` | 必填，源文件（`.exe`/`.dll` 或 `.ico`）。 |
| `out` / `--out` | 输出 `.ico` 路径（可选）。不指定时自动命名（PE → `<名>.ico`；ico → `<名>_opt.ico`）。 |
| `--list` | 列出图标组 / 尺寸信息，不生成文件。 |
| `--all` | （仅 PE）导出**所有**图标组，每组生成一个独立的 `.ico` 文件。 |
| `--index N` | （仅 PE）指定要提取的组序号，默认 `0`（第一组）。 |
| `--fill` | 补齐尺寸。不带值 = 推荐 10 档；也可 `--fill 20,40,96,128` 指定；`all` / `recommended` 同推荐档。 |
| `--force` | 配合 `--fill`：用母版重采样**覆盖**已存在的同尺寸图像（统一升级为 32 位高清版）。 |

---

## 工作原理（简述）

1. **提取**：通过 `LoadLibraryExW(LOAD_LIBRARY_AS_DATAFILE)` 以数据方式加载 PE，枚举 `RT_GROUP_ICON` 资源，把每个 `RT_ICON` 的原始字节按 ICO 容器格式（ICONDIR + ICONDIRENTRY + 图像数据）重组写出。
2. **补齐**：解码图标内分辨率最高的那幅图（支持 PNG 与经典 DIB：32/24/8/4/1 bpp，含调色板与 AND 掩码透明），用**预乘 alpha 的面积平均法**降采样到目标尺寸，再以 32 位非压缩 `ICONIMAGE` 编码追加进同一个 `.ico`。
3. **去重**：目标尺寸已存在时默认跳过（`--force` 时改为覆盖）。

---

## 注意事项与限制

- **仅支持 Windows**：依赖 Win32 API，无法在 Linux / macOS 上运行。
- **ICO 最大 256×256**：`.ico` 容器单字节宽高字段决定了格式上限就是 256（256 用 `0` 作哨兵）。因此 `--fill` 只接受 **≤ 256** 的尺寸；传入 `512` 等会直接报错提示改用 PNG / icns。
- **`--all` 产出多个文件**：一个 `.ico` 只能装一个图标组，所以全导出必然是“每组一个 `.ico`”（`<名>_group<N>.ico`）。
- **母版不足会放大**：若源图标最大只有 48×48，却要求补 256，会用 48 放大到 256（质量有限，脚本会打印警告）。
- **第一组通常即主程序图标**：普通应用程序的“第一组”一般就是主图标；图标库型 DLL（含成百上千组）请用 `--list` / `--index` / `--all` 精确定位。

---

## License

[MIT](https://opensource.org/licenses/MIT) — 可自由使用、修改、再分发。
