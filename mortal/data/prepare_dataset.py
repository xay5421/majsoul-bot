#!/usr/bin/env python3
"""
天凤凤凰卓牌谱 → Mortal mjai json.gz 数据集准备脚本

用法:
  python prepare_dataset.py [--download] [--convert] [--all]
  
  --download  下载天凤牌谱 zip
  --convert   解压并转换为 mjai json.gz
  --all       下载+转换（默认）

数据目录结构:
  data/
    raw/          天凤原始 zip
    mjlog/        解压后的 mjlog 文件
    mjai/         转换后的 mjai json.gz (按年份子目录)
      2019/
      2020/
      ...
"""

import os
import sys
import gzip
import json
import zipfile
import argparse
import traceback
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

# 把当前目录加入 path 以便导入 mjlog2mjai
sys.path.insert(0, str(Path(__file__).parent))
from mjlog2mjai import load_mjlog, parse_mjlog_to_mjai

BASE_DIR = Path(__file__).parent
RAW_DIR = BASE_DIR / "raw"
MJLOG_DIR = BASE_DIR / "mjlog"
MJAI_DIR = BASE_DIR / "mjai"

# 天凤凤凰卓四人赤ドラ牌谱 URL (2019-2025)
YEARS = list(range(19, 26))  # n19 ~ n25
DOWNLOAD_URL = "https://tenhou.net/0/log/mjlog_pf4-20_n{year}.zip"


def download_all():
    """下载天凤牌谱"""
    import urllib.request
    
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    
    for y in YEARS:
        url = DOWNLOAD_URL.format(year=y)
        filename = f"mjlog_pf4-20_n{y}.zip"
        filepath = RAW_DIR / filename
        
        if filepath.exists():
            size_mb = filepath.stat().st_size / 1024 / 1024
            # 检查是否下载完整（最小的文件也有 12MB）
            if size_mb > 10:
                print(f"  {filename} 已存在 ({size_mb:.1f}MB)，跳过")
                continue
            else:
                print(f"  {filename} 不完整 ({size_mb:.1f}MB)，重新下载")
        
        print(f"  下载 {url} ...")
        try:
            urllib.request.urlretrieve(url, filepath)
            size_mb = filepath.stat().st_size / 1024 / 1024
            print(f"  ✓ {filename} ({size_mb:.1f}MB)")
        except Exception as e:
            print(f"  ✗ {filename} 下载失败: {e}")
            if filepath.exists():
                filepath.unlink()


def extract_all():
    """解压 zip 到 mjlog 目录"""
    MJLOG_DIR.mkdir(parents=True, exist_ok=True)
    
    for zippath in sorted(RAW_DIR.glob("mjlog_pf4-20_n*.zip")):
        year = zippath.stem.split("_n")[-1]
        year_dir = MJLOG_DIR / f"20{year}"
        
        if year_dir.exists() and any(year_dir.iterdir()):
            count = sum(1 for _ in year_dir.glob("*.mjlog"))
            print(f"  20{year}: 已解压 ({count} 局)，跳过")
            continue
        
        year_dir.mkdir(parents=True, exist_ok=True)
        print(f"  解压 {zippath.name} → {year_dir} ...")
        
        try:
            with zipfile.ZipFile(zippath, 'r') as zf:
                count = 0
                for name in zf.namelist():
                    if name.endswith('.mjlog'):
                        # 只取文件名，忽略子目录
                        basename = os.path.basename(name)
                        if basename:
                            data = zf.read(name)
                            (year_dir / basename).write_bytes(data)
                            count += 1
                print(f"  ✓ 20{year}: {count} 局")
        except Exception as e:
            print(f"  ✗ 20{year}: 解压失败: {e}")


def convert_single_mjlog(mjlog_path: str, output_dir: str) -> str:
    """转换单个 mjlog 文件为 mjai json.gz"""
    mjlog_path = Path(mjlog_path)
    output_dir = Path(output_dir)
    
    stem = mjlog_path.stem
    output_path = output_dir / f"{stem}.json.gz"
    
    if output_path.exists():
        return f"skip:{stem}"
    
    try:
        root = load_mjlog(str(mjlog_path))
        mjai_text = parse_mjlog_to_mjai(root)
        
        with gzip.open(output_path, 'wt', encoding='utf-8') as f:
            f.write(mjai_text)
        
        return f"ok:{stem}"
    except NotImplementedError as e:
        # 三人麻将等不支持的格式
        return f"skip_unsupported:{stem}:{e}"
    except Exception as e:
        return f"error:{stem}:{e}"


def convert_all(workers=None):
    """转换所有 mjlog → mjai json.gz"""
    import multiprocessing
    if workers is None:
        workers = min(multiprocessing.cpu_count(), 8)
    
    MJAI_DIR.mkdir(parents=True, exist_ok=True)
    
    for year_dir in sorted(MJLOG_DIR.iterdir()):
        if not year_dir.is_dir():
            continue
        
        year = year_dir.name
        output_dir = MJAI_DIR / year
        output_dir.mkdir(parents=True, exist_ok=True)
        
        mjlog_files = list(year_dir.glob("*.mjlog"))
        if not mjlog_files:
            continue
        
        # 检查已转换数量
        existing = sum(1 for _ in output_dir.glob("*.json.gz"))
        if existing >= len(mjlog_files):
            print(f"  {year}: 已全部转换 ({existing} 局)，跳过")
            continue
        
        print(f"  {year}: 转换 {len(mjlog_files)} 局 ({existing} 已完成) ...")
        
        ok_count = 0
        skip_count = existing
        error_count = 0
        
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(convert_single_mjlog, str(f), str(output_dir)): f
                for f in mjlog_files
            }
            
            for i, future in enumerate(as_completed(futures)):
                result = future.result()
                if result.startswith("ok:"):
                    ok_count += 1
                elif result.startswith("skip:"):
                    skip_count += 1
                elif result.startswith("error:"):
                    error_count += 1
                    if error_count <= 5:
                        print(f"    错误: {result}")
                
                done = i + 1
                if done % 500 == 0 or done == len(futures):
                    print(f"    进度: {done}/{len(futures)} (成功={ok_count}, 跳过={skip_count}, 错误={error_count})")
        
        total = sum(1 for _ in output_dir.glob("*.json.gz"))
        print(f"  ✓ {year}: {total} 局 mjai json.gz")


def show_stats():
    """显示数据集统计"""
    print("\n📊 数据集统计:")
    total_files = 0
    total_size = 0
    
    if MJAI_DIR.exists():
        for year_dir in sorted(MJAI_DIR.iterdir()):
            if year_dir.is_dir():
                files = list(year_dir.glob("*.json.gz"))
                count = len(files)
                size = sum(f.stat().st_size for f in files)
                size_mb = size / 1024 / 1024
                total_files += count
                total_size += size
                print(f"  {year_dir.name}: {count:>6} 局 ({size_mb:.1f}MB)")
    
    total_mb = total_size / 1024 / 1024
    print(f"  {'合计':>4}: {total_files:>6} 局 ({total_mb:.1f}MB)")
    print(f"\n  数据路径: {MJAI_DIR}")


def main():
    parser = argparse.ArgumentParser(description="天凤牌谱 → Mortal mjai 数据集准备")
    parser.add_argument("--download", action="store_true", help="下载天凤牌谱")
    parser.add_argument("--convert", action="store_true", help="解压并转换为 mjai")
    parser.add_argument("--all", action="store_true", help="下载+转换（默认）")
    parser.add_argument("--stats", action="store_true", help="显示统计")
    parser.add_argument("--workers", type=int, default=None, help="转换并发数")
    args = parser.parse_args()
    
    if not any([args.download, args.convert, args.all, args.stats]):
        args.all = True
    
    if args.all or args.download:
        print("📥 下载天凤凤凰卓牌谱...")
        download_all()
    
    if args.all or args.convert:
        print("\n📦 解压牌谱...")
        extract_all()
        print("\n🔄 转换为 mjai json.gz...")
        convert_all(workers=args.workers)
    
    show_stats()


if __name__ == "__main__":
    main()
