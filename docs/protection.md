# Protecting the archive

**English** · [中文](#防護與修復建議)

OverCompressing **detects** damage — every stored file has a SHA-256, every frame a CRC-32, and `verify` checks the
whole dependency chain — but it does **not repair** it. Repair is left to whatever protection fits your storage; this
page compares common options and lists what matters for this kind of archive.

## What matters for this archive

1. **Protect bases more than anything else.** A fine-tune stored as a delta, and any file stored as a `same`
   reference, cannot be restored without the files they point to. A damaged base shard breaks every model built on
   it, so give base repos the most copies or redundancy.
2. **Regenerate repair data after every re-pack.** `pack` and `rebase` rewrite a repo's stored files; recovery data
   made earlier now describes old contents, and using it would "repair" files back to them. OverCompressing never
   deletes files it did not create, so remove or rebuild such data yourself.
3. **Protect `_kit/` too**, and keep a copy of it with every copy of the archive.
4. **Verify regularly and after every repair**: `python -I tools/llm_archive.py verify <archive_root>`.

## Options

| approach | protects against | cost | notes |
|---|---|---|---|
| **Several copies on different media and in different places (3-2-1)** | everything, including losing a whole disk | 100 % per copy | simplest and strongest; verify each copy |
| **PAR2** ([par2cmdline](https://github.com/Parchive/par2cmdline), [MultiPar](https://github.com/Yutaka-Sawada/MultiPar), [ParPar](https://github.com/animetosho/ParPar)) | scattered bit rot, lost blocks up to the chosen redundancy | chosen %, e.g. 5–10 % | open format with several independent tools; separate files next to the data |
| **RAR recovery record** (WinRAR) | bit rot inside the RAR file | chosen % | needs the files wrapped in RAR; proprietary format |
| **Self-healing file systems**: ZFS (mirror/RAIDZ + scrub), Btrfs (RAID1 + scrub), ReFS with Storage Spaces mirror/parity | bit rot on live disks, repaired automatically | the volume's redundancy | best for a NAS or server that stays powered |
| **Optical media + ECC** ([dvdisaster](https://dvdisaster.jcea.es/)), M-DISC | scratches and aging of CD/DVD/BD | chosen % | good for long-term offline copies |
| **LTO tape** | built-in ECC, decades of shelf life | drive cost | for large collections |

A sensible combination for a model collection: two or more copies on different media (one off-site), PAR2 or a
self-healing file system on each, and a periodic `verify`.

## Example: PAR2 per repo

Run inside one repo directory of the archive (e.g. `archive/Qwen__Qwen2.5-7B/`). Protect `manifest.json` and every
stored file, i.e. the `stored` field of each manifest entry:

```bash
# list the files to protect
python -c "import json; m = json.load(open('manifest.json')); print('\n'.join(['manifest.json'] + [e['stored'] for e in m['files'] if e.get('stored')]))" > protect.txt

# create 5 % recovery data (bash)
xargs -d '\n' par2 create -r5 -n1 -B. recovery.par2 < protect.txt
```

```powershell
# create 5 % recovery data (PowerShell)
par2 create -r5 -n1 -B. recovery.par2 (Get-Content protect.txt)
```

Later:

```bash
par2 verify recovery.par2                                          # detect
par2 repair recovery.par2                                          # repair
python -I <path>/llm_archive.py verify <archive_root> <name>       # confirm with OverCompressing
```

During development, a single flipped byte in a 630 MB container was found and repaired this way with 5 % PAR2 data
(par2cmdline 1.4.0: 1 of 1991 blocks damaged, repaired, SHA-256 restored).

After repairing, par2 leaves the damaged originals as `*.1` files; delete them once `verify` passes. After a re-pack
or a `rebase` that changed the repo, delete `recovery*.par2` and create it again.

---

# 防護與修復建議

[English](#protecting-the-archive) · **中文**

OverCompressing 只負責**偵測**損毀：每個儲存檔都有 SHA-256、每個區塊都有 CRC-32，`verify` 會檢查整條依賴鏈。
它**不負責修復**。修復交給最適合你儲存方式的方案；本頁比較常見的做法，並列出這類歸檔需要特別注意的地方。

## 這類歸檔要注意的事

1. **base 最需要保護。** 以 delta 或 `same` 引用儲存的檔案，少了它指向的檔案就無法還原；base 的一個分片損毀，
   會連帶影響所有以它為基礎的模型，所以 base repo 應該有最多副本或最高冗餘。
2. **每次重新打包後都要重建修復資料。** `pack` 與 `rebase` 會改寫該 repo 的儲存檔，舊的修復資料描述的是舊內容，
   拿它修復會把檔案改回舊版。本工具不會刪除不是它建立的檔案，請自行移除或重建。
3. **`_kit/` 也要保護**，每一份歸檔副本都要附上它。
4. **定期驗證，修復後也要驗證**：`python -I tools/llm_archive.py verify <archive_root>`。

## 方案比較

| 方案 | 防護範圍 | 成本 | 備註 |
|---|---|---|---|
| **多份副本、不同媒體、不同地點（3-2-1）** | 全部，包含整顆硬碟損失 | 每份 100% | 最簡單也最可靠；每份都要驗證 |
| **PAR2**（[par2cmdline](https://github.com/Parchive/par2cmdline)、[MultiPar](https://github.com/Yutaka-Sawada/MultiPar)、[ParPar](https://github.com/animetosho/ParPar)） | 零星位元腐壞、遺失區塊（在冗餘比例內） | 自訂比例，例如 5–10% | 開放格式、有多個獨立實作；與資料分開存放 |
| **RAR 修復記錄**（WinRAR） | RAR 檔內的位元腐壞 | 自訂比例 | 需把檔案包成 RAR；專有格式 |
| **具自我修復能力的檔案系統**：ZFS（mirror/RAIDZ + scrub）、Btrfs（RAID1 + scrub）、ReFS 搭配儲存空間的鏡像／同位 | 線上磁碟的位元腐壞，自動修復 | 視磁碟區的冗餘設定 | 適合持續運作的 NAS 或伺服器 |
| **光碟 + ECC**（[dvdisaster](https://dvdisaster.jcea.es/)）、M-DISC | CD/DVD/BD 的刮傷與老化 | 自訂比例 | 適合長期離線保存 |
| **LTO 磁帶** | 內建 ECC、可保存數十年 | 磁帶機成本 | 適合大量收藏 |

建議組合：兩份以上、不同媒體（其中一份放在異地），每份再搭配 PAR2 或自我修復檔案系統，並定期 `verify`。

## 範例：每個 repo 一份 PAR2

在歸檔的某個 repo 資料夾內執行（例如 `archive/Qwen__Qwen2.5-7B/`），保護 `manifest.json` 與每個儲存檔
（也就是 manifest 中各項的 `stored` 欄位）：

```bash
# 列出要保護的檔案
python -c "import json; m = json.load(open('manifest.json')); print('\n'.join(['manifest.json'] + [e['stored'] for e in m['files'] if e.get('stored')]))" > protect.txt

# 建立 5% 修復資料（bash）
xargs -d '\n' par2 create -r5 -n1 -B. recovery.par2 < protect.txt
```

```powershell
# 建立 5% 修復資料（PowerShell）
par2 create -r5 -n1 -B. recovery.par2 (Get-Content protect.txt)
```

之後：

```bash
par2 verify recovery.par2                                          # 檢查
par2 repair recovery.par2                                          # 修復
python -I <路徑>/llm_archive.py verify <archive_root> <名稱>        # 用本工具確認
```

開發期間實測：630 MB 容器檔中翻轉 1 個 byte，用 5% 的 PAR2 資料（par2cmdline 1.4.0）成功找出並修復（1991 個區塊
壞 1 個），SHA-256 恢復正確。

修復後 par2 會把損毀的原檔留成 `*.1`，`verify` 通過後再刪除。重新打包後，或 `rebase` 改變了該 repo 之後，請刪除
`recovery*.par2` 並重新建立。
