# Protecting the archive

**English** · [中文](#防護與修復建議)

OverCompressing **detects** damage — every stored file (blob) ends with its own SHA-256, every frame has a CRC-32,
and `verify` checks the whole chain of bases — but it does **not repair** it. Bit-level damage is the job of the
hardware and the file system (RAID, ECC, ZFS/Btrfs/ReFS); if those cannot stop it, little else will. This page
compares common options and lists what matters for this kind of pack.

## What matters for this archive

1. **Protect bases more than anything else.** A fine-tune stored as a delta cannot be restored without the blobs it
   is coded against, and a file several models share is one blob. A damaged base blob breaks every model built on
   it, so give the pack the most copies or redundancy you can.
2. **Regenerate repair data after every change.** `pack`, `remove` and `rebase` add, replace and delete blobs;
   recovery data made earlier now describes old contents. OverCompressing never deletes files it did not create, so
   remove or rebuild such data yourself. A pack file never changes: make its repair data once.
3. **Keep the tool with the pack**: a portable copy in the pack folder as `_kit/` (see the build guides), protected
   like the rest and kept with every copy of the pack.
4. **Verify regularly and after every repair**: `python -I tools/llm_archive.py verify <pack>`.
5. **When verify fails, read its damage report** (usage, section 4): it says which blobs are at fault, which files
   are lost because of them (their own blob, or one they are coded against), and what to do. Storing the original of
   a file again (`pack … --add --file`) heals it and everything coded against it, so keep, or know where to get,
   the originals of your bases; the report gives each lost file's SHA-256 to find it by. What cannot be healed can be
   given up with `remove`, or in the window with − Clean up.

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

## Example: PAR2 for a pack file

A pack file (`.oczip`) is one file that never changes: the simplest thing to protect. Next to it:

```bash
par2 create -r5 -n1 models.oczip.par2 models.oczip                # create 5 % recovery data
par2 verify models.oczip.par2                                      # detect
par2 repair models.oczip.par2                                      # repair
python -I <path>/llm_archive.py verify models.oczip                # confirm with OverCompressing
```

During development, a single flipped byte in a 630 MB stored file was found and repaired this way with 5 % PAR2 data
(par2cmdline 1.4.0: 1 of 1991 blocks damaged, repaired, SHA-256 restored).

After repairing, par2 leaves the damaged original as a `*.1` file; delete it once `verify` passes. For a pack folder
that is still changing, a self-healing file system or copies fit better than PAR2, which would have to be rebuilt
after every change.

---

# 防護與修復建議

[English](#protecting-the-archive) · **中文**

OverCompressing 只負責**偵測**損毀：每個儲存檔（blob）結尾都有它自己的 SHA-256、每個區塊都有 CRC-32，`verify` 會檢查整條
基底鏈。它**不負責修復**。位元層級的損毀是硬體與檔案系統（RAID、ECC、ZFS/Btrfs/ReFS）的工作；如果它們擋不住，其他方案也幫不了
多少。本頁比較常見的做法，並列出這類 pack 需要特別注意的地方。

## 這類歸檔要注意的事

1. **base 最需要保護。** 以差異方式存放的微調模型，少了它所依據的 blob 就無法還原；多個模型共用的檔案也只有一個 blob。
   base 的一個 blob 損毀，會連帶影響所有以它為基礎的模型，所以請給 pack 盡可能多的副本或冗餘。
2. **每次變更後都要重建修復資料。** `pack`、`remove` 與 `rebase` 會新增、取代、刪除 blob，舊的修復資料描述的是舊內容。
   本工具不會刪除不是它建立的檔案，請自行移除或重建。pack 檔永遠不會改變：修復資料做一次就好。
3. **讓 pack 帶著工具**：把可攜版放在 pack 資料夾裡、命名為 `_kit/`（見建置頁面），和其他部分一樣保護，每一份 pack
   副本都要附上它。
4. **定期驗證，修復後也要驗證**：`python -I tools/llm_archive.py verify <pack>`。
5. **verify 失敗時，請看它的損毀報告**（使用說明第 4 節）：它會說明哪些 blob 出了問題、哪些檔案因此遺失（自己的 blob 壞了，
   或它所依據的 blob 壞了），以及該怎麼處理。把檔案的原檔重新存進去（`pack … --add --file`），它和所有以它為基礎的檔案都會
   一起恢復，所以請保留 base 的原檔，或至少知道去哪裡取得；報告會列出每個遺失檔案的 SHA-256，方便找回。救不回來的，可以用
   `remove` 放棄，或在視窗中用 − Clean up 處理。

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

## 範例：為 pack 檔加上 PAR2

pack 檔（`.oczip`）是一個永遠不會改變的檔案，最容易保護。在它旁邊執行：

```bash
par2 create -r5 -n1 models.oczip.par2 models.oczip                # 建立 5% 修復資料
par2 verify models.oczip.par2                                      # 檢查
par2 repair models.oczip.par2                                      # 修復
python -I <路徑>/llm_archive.py verify models.oczip                # 用本工具確認
```

開發期間實測：630 MB 儲存檔中翻轉 1 個 byte，用 5% 的 PAR2 資料（par2cmdline 1.4.0）成功找出並修復（1991 個區塊
壞 1 個），SHA-256 恢復正確。

修復後 par2 會把損毀的原檔留成 `*.1`，`verify` 通過後再刪除。仍在變動中的 pack 資料夾，比起每次變更後都得重建的 PAR2，
更適合用自我修復檔案系統或多份副本。
