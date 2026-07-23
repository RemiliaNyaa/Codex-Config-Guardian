#!/usr/bin/env python3
"""
Codex Config Guardian
=====================
保护 Codex config.toml 中的用户设置不被 cc-switch 全量覆盖。

工作原理：
  1. 持续轮询 ~/.codex/config.toml（每 2 秒）
  2. 当配置处于"良好状态"（用户字段齐全）时，保存用户配置快照（baseline）
  3. 当检测到 cc-switch 全量覆盖（用户字段消失 + 代理标记存在）时：
     将 baseline 中的用户段合并回 config.toml，保留代理字段不变

合并策略：
  - 代理字段（model_provider / model / [model_providers] 等）：保留 cc-switch 的值，不动
  - MCP 服务器（[mcp_servers]）：只补充缺失的 server，不动已有的（保留 Codex 动态写入的管道路径等）
  - 其他所有段（[desktop] / [features] / [memories] / personality 等）：以 baseline 为准

用法：
  python codex_config_guardian.py            # 守护模式，持续运行
  python codex_config_guardian.py --once     # 只检查并合并一次，然后退出
  python codex_config_guardian.py --status   # 查看当前状态
"""

import argparse
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import tomlkit
    from tomlkit.exceptions import ParseError
except ImportError:
    print("错误: 缺少 tomlkit 依赖。请运行: python -m pip install tomlkit")
    sys.exit(1)

# ============================================================
# 配置
# ============================================================

CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
CONFIG_PATH = CODEX_HOME / "config.toml"

GUARDIAN_DIR = CODEX_HOME / ".config-guardian"
BASELINE_PATH = GUARDIAN_DIR / "user_baseline.toml"
BACKUP_DIR = GUARDIAN_DIR / "backups"
LOG_PATH = GUARDIAN_DIR / "guardian.log"

POLL_INTERVAL = 2  # 轮询间隔（秒）
MAX_RETRIES = 5  # 文件读写重试次数
RETRY_DELAY = 0.3  # 重试延迟（秒）

# cc-switch 代理管理的顶层字段（合并时跳过，保留 cc-switch 写入的值）
PROXY_MANAGED_TOP_KEYS = frozenset({
    "model_provider",
    "model",
    "model_reasoning_effort",
    "disable_response_storage",
    "experimental_bearer_token",
})

# cc-switch 代理管理的段（整个段跳过）
PROXY_MANAGED_SECTIONS = frozenset({
    "model_providers",
})

# MCP 段：只补充缺失的 server，不覆盖已有的（保留 Codex 动态写入的管道路径等）
MCP_SERVERS_KEY = "mcp_servers"

# 代理标记（出现这些字符串说明代理接管处于激活状态）
PROXY_MARKERS = ("PROXY_MANAGED", "127.0.0.1:1572")


# ============================================================
# 日志
# ============================================================

def setup_logging(verbose: bool = False):
    GUARDIAN_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ============================================================
# 文件读写（带重试，处理 Windows 文件锁）
# ============================================================

def read_text(path: Path) -> str:
    for attempt in range(MAX_RETRIES):
        try:
            return path.read_text(encoding="utf-8")
        except (PermissionError, OSError) as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise

def write_text_atomic(path: Path, content: str):
    """原子写入：先写临时文件，再 rename，避免中途崩溃导致文件损坏"""
    tmp = path.with_suffix(".tmp")
    for attempt in range(MAX_RETRIES):
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(str(tmp), str(path))
            return
        except (PermissionError, OSError) as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise

def parse_toml_safe(text: str):
    """安全解析 TOML，失败时返回 None"""
    try:
        return tomlkit.parse(text)
    except ParseError as e:
        logging.debug(f"TOML 解析失败（文件可能正在写入中）: {e}")
        return None


# ============================================================
# 代理状态检测
# ============================================================

def is_proxy_active(config_text: str) -> bool:
    """检测 config.toml 是否处于 cc-switch 代理接管状态"""
    return any(marker in config_text for marker in PROXY_MARKERS)


# ============================================================
# Baseline 管理
# ============================================================

def build_baseline(current_doc) -> "tomlkit.TOMLDocument":
    """
    从当前配置构建 baseline（用户配置快照）。
    剥离所有代理管理的字段，只保留用户/Codex 管理的段。
    """
    baseline = tomlkit.document()
    for key, value in current_doc.items():
        if key in PROXY_MANAGED_TOP_KEYS:
            continue
        if key in PROXY_MANAGED_SECTIONS:
            continue
        baseline[key] = value
    return baseline

def save_baseline(doc):
    GUARDIAN_DIR.mkdir(parents=True, exist_ok=True)
    write_text_atomic(BASELINE_PATH, tomlkit.dumps(doc))
    logging.debug(f"Baseline 已更新: {BASELINE_PATH}")

def load_baseline():
    if not BASELINE_PATH.exists():
        return None
    text = read_text(BASELINE_PATH)
    return parse_toml_safe(text)

def get_baseline_keys(baseline_doc) -> set:
    """获取 baseline 中所有非代理管理的顶层键"""
    if not baseline_doc:
        return set()
    return {
        k for k in baseline_doc.keys()
        if k not in PROXY_MANAGED_TOP_KEYS and k not in PROXY_MANAGED_SECTIONS
    }


# ============================================================
# 合并逻辑
# ============================================================

def deep_merge_dict(current, baseline):
    """
    深度合并：baseline 中的值优先，但保留 current 中独有的键。
    用于 [desktop]、[features] 等用户段的合并。
    """
    result = tomlkit.table()
    # 先复制 current 的所有键
    for k, v in current.items():
        result[k] = v
    # 用 baseline 的值覆盖（baseline 优先）
    for k, v in baseline.items():
        if k in result and hasattr(result[k], "items") and hasattr(v, "items"):
            result[k] = deep_merge_dict(result[k], v)
        else:
            result[k] = v
    return result

def merge_mcp_servers(current_mcp, baseline_mcp):
    """
    MCP 服务器合并：只补充 baseline 中有但 current 中没有的 server。
    已有的 server 不动（保留 Codex 动态写入的管道路径、版本号等）。
    """
    result = tomlkit.table()
    # 先复制 current 的所有 server
    for name, config in current_mcp.items():
        result[name] = config
    # 补充 baseline 中缺失的 server
    for name, config in baseline_mcp.items():
        if name not in result:
            logging.info(f"  恢复缺失的 MCP 服务器: {name}")
            result[name] = config
    return result

def merge_config(current_doc, baseline_doc):
    """
    将 baseline 合并到 current：
    - 代理字段：跳过（保留 cc-switch 的值）
    - MCP 服务器：只补充缺失的
    - 其他段：baseline 优先（深度合并）
    """
    result = tomlkit.document()
    # 先复制 current 的所有内容（包括代理字段）
    for key, value in current_doc.items():
        result[key] = value
    # 用 baseline 覆盖用户管理的段
    for key, baseline_value in baseline_doc.items():
        if key in PROXY_MANAGED_TOP_KEYS:
            continue
        if key in PROXY_MANAGED_SECTIONS:
            continue
        if key == MCP_SERVERS_KEY:
            if key in result:
                result[key] = merge_mcp_servers(result[key], baseline_value)
            else:
                result[key] = baseline_value
            continue
        # 其他段：深度合并（baseline 优先）
        if key in result and hasattr(result[key], "items") and hasattr(baseline_value, "items"):
            result[key] = deep_merge_dict(result[key], baseline_value)
        else:
            result[key] = baseline_value
    return result

def has_missing_user_keys(current_doc, baseline_doc) -> list:
    """
    检查 current 是否缺少 baseline 中存在的用户管理键。
    返回缺失的键名列表（空列表表示无缺失）。
    """
    missing = []
    if not baseline_doc:
        return missing
    for key in baseline_doc.keys():
        if key in PROXY_MANAGED_TOP_KEYS or key in PROXY_MANAGED_SECTIONS:
            continue
        if key not in current_doc:
            missing.append(key)
    return missing

def config_has_all_baseline_keys(current_doc, baseline_doc) -> bool:
    """检查 current 是否包含 baseline 中的所有用户管理键"""
    return len(has_missing_user_keys(current_doc, baseline_doc)) == 0


# ============================================================
# 备份
# ============================================================

def backup_config(reason: str = ""):
    """在修改 config.toml 前备份"""
    if not CONFIG_PATH.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"config_{ts}.toml"
    shutil.copy2(str(CONFIG_PATH), str(BACKUP_DIR / backup_name))
    # 只保留最近 20 个备份
    backups = sorted(BACKUP_DIR.glob("config_*.toml"))
    for old in backups[:-20]:
        old.unlink()
    logging.debug(f"已备份 config.toml -> {backup_name} ({reason})")


# ============================================================
# 核心处理逻辑
# ============================================================

def process_once(verbose: bool = False):
    """
    执行一次检查周期：
    1. 读取 config.toml
    2. 如果代理未激活：更新 baseline
    3. 如果代理激活且用户字段缺失：合并 baseline 回 config.toml
    返回: "merged" | "baseline_updated" | "skipped" | "error"
    """
    if not CONFIG_PATH.exists():
        logging.debug("config.toml 不存在，跳过")
        return "skipped"

    config_text = read_text(CONFIG_PATH)
    current_doc = parse_toml_safe(config_text)
    if current_doc is None:
        return "skipped"  # TOML 解析失败，等下次重试

    baseline_doc = load_baseline()
    proxy_active = is_proxy_active(config_text)

    # --- 情况 1：代理未激活 ---
    # config.toml 是用户的真实配置，更新 baseline
    if not proxy_active:
        new_baseline = build_baseline(current_doc)
        # 检查是否有实质变化（避免频繁写入）
        old_text = tomlkit.dumps(baseline_doc) if baseline_doc else ""
        new_text = tomlkit.dumps(new_baseline)
        if new_text != old_text:
            save_baseline(new_baseline)
            if verbose:
                logging.info("代理未激活，baseline 已更新")
            return "baseline_updated"
        return "skipped"

    # --- 情况 2：代理激活 ---
    missing_keys = has_missing_user_keys(current_doc, baseline_doc)

    if missing_keys:
        # cc-switch 全量覆盖了配置，用户字段丢失 -> 合并
        logging.warning(
            f"检测到 cc-switch 全量覆盖！缺失字段: {', '.join(missing_keys)}"
        )
        logging.info("开始合并用户配置...")

        backup_config("merge_before")

        merged_doc = merge_config(current_doc, baseline_doc)
        merged_text = tomlkit.dumps(merged_doc)

        write_text_atomic(CONFIG_PATH, merged_text)
        logging.info("✓ 用户配置已恢复")

        # 合并后更新 baseline（捕获最新状态）
        new_baseline = build_baseline(merged_doc)
        save_baseline(new_baseline)

        return "merged"

    # --- 情况 3：代理激活，用户字段齐全 ---
    # 正常状态，更新 baseline 以捕获用户最新设置
    # （用户可能在 Codex UI 中修改了桌面设置、字号等）
    new_baseline = build_baseline(current_doc)
    old_text = tomlkit.dumps(baseline_doc) if baseline_doc else ""
    new_text = tomlkit.dumps(new_baseline)
    if new_text != old_text:
        save_baseline(new_baseline)
        logging.debug("代理激活，用户字段齐全，baseline 已更新")
        return "baseline_updated"

    return "skipped"


# ============================================================
# 状态查看
# ============================================================

def show_status():
    print("=" * 60)
    print("Codex Config Guardian - 状态")
    print("=" * 60)
    print(f"配置文件:    {CONFIG_PATH}")
    print(f"Baseline:    {BASELINE_PATH}")
    print(f"备份目录:    {BACKUP_DIR}")
    print(f"日志文件:    {LOG_PATH}")
    print()

    if not CONFIG_PATH.exists():
        print("⚠ config.toml 不存在")
        return

    config_text = read_text(CONFIG_PATH)
    current_doc = parse_toml_safe(config_text)
    if current_doc is None:
        print("⚠ config.toml 解析失败")
        return

    proxy = is_proxy_active(config_text)
    print(f"代理状态:    {'激活' if proxy else '未激活'}")
    print(f"顶层字段:    {list(current_doc.keys())}")

    baseline_doc = load_baseline()
    if baseline_doc:
        print(f"Baseline 字段: {list(baseline_doc.keys())}")
        missing = has_missing_user_keys(current_doc, baseline_doc)
        if missing:
            print(f"⚠ 缺失字段:   {', '.join(missing)}")
            print("  -> 下次检查将自动合并")
        else:
            print("✓ 所有用户字段齐全")
    else:
        print("⚠ Baseline 尚未创建（代理未激活过？）")

    print()
    backups = sorted(BACKUP_DIR.glob("config_*.toml"), reverse=True) if BACKUP_DIR.exists() else []
    print(f"备份数量:    {len(backups)}")
    if backups:
        print(f"最近备份:    {backups[0].name}")


# ============================================================
# 主循环
# ============================================================

def run_daemon(verbose: bool = False):
    logging.info("=" * 50)
    logging.info("Codex Config Guardian 已启动")
    logging.info(f"  配置文件: {CONFIG_PATH}")
    logging.info(f"  Baseline: {BASELINE_PATH}")
    logging.info(f"  轮询间隔: {POLL_INTERVAL}s")
    logging.info("=" * 50)

    # 首次运行立即检查一次
    try:
        result = process_once(verbose=True)
        if result == "merged":
            logging.info("启动时检测到并修复了配置覆盖")
        elif result == "baseline_updated":
            logging.info("启动时已更新 baseline")
    except Exception as e:
        logging.error(f"启动检查失败: {e}", exc_info=True)

    # 轮询循环
    last_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0

    while True:
        time.sleep(POLL_INTERVAL)
        try:
            if not CONFIG_PATH.exists():
                continue
            current_mtime = CONFIG_PATH.stat().st_mtime
            if current_mtime == last_mtime:
                continue  # 文件未变化

            last_mtime = current_mtime
            result = process_once(verbose=verbose)

            if result == "merged":
                last_mtime = CONFIG_PATH.stat().st_mtime  # 更新 mtime（我们自己写了文件）
            elif result == "baseline_updated" and verbose:
                logging.debug("Baseline 已更新")
        except Exception as e:
            logging.error(f"处理异常: {e}", exc_info=True)
            time.sleep(POLL_INTERVAL * 2)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Codex Config Guardian - 保护用户配置不被 cc-switch 全量覆盖"
    )
    parser.add_argument("--once", action="store_true", help="只检查并合并一次，然后退出")
    parser.add_argument("--status", action="store_true", help="查看当前状态")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    if args.status:
        show_status()
        return

    if args.once:
        result = process_once(verbose=True)
        print(f"结果: {result}")
        return

    try:
        run_daemon(verbose=args.verbose)
    except KeyboardInterrupt:
        logging.info("Guardian 已停止")


if __name__ == "__main__":
    main()
