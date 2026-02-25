"""测试 Mortal 重放是否正确同步状态"""
import subprocess
import os
import json
import sys

MORTAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Mortal", "mortal")
PYTHON = "/home/ubuntu/workspace/majsoul-bot/.venv/bin/python"

def start_mortal(seat=0):
    proc = subprocess.Popen(
        [PYTHON, "mortal.py", str(seat)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=MORTAL_DIR,
        text=True,
    )
    return proc

def send(proc, event):
    line = json.dumps(event)
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    resp = proc.stdout.readline().strip()
    data = json.loads(resp) if resp else None
    return data

def test_replay():
    """模拟一个简单的重放场景，检查 Mortal 是否能正确跟踪手牌"""
    proc = start_mortal(0)
    
    # start_game
    r = send(proc, {"type": "start_game"})
    print(f"start_game → {r}")
    
    # start_kyoku: 庄家 seat=0, 13张手牌
    tehais = [
        ["1m","2m","3m","4m","5m","1p","2p","3p","1s","2s","3s","E","S"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
    ]
    r = send(proc, {
        "type": "start_kyoku",
        "bakaze": "E", "dora_marker": "1s", "kyoku": 1, "honba": 0, "kyotaku": 0,
        "oya": 0, "scores": [25000,25000,25000,25000], "tehais": tehais,
    })
    print(f"start_kyoku → {r}")
    
    # 庄家摸牌 (第14张)
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "W"})
    print(f"tsumo(0, W) → {r}")
    mortal_wants = r.get("pai") if r else "?"
    print(f"  Mortal 想打: {mortal_wants}")
    
    # 但实际打了 E (不同于 Mortal 的决策)
    r = send(proc, {"type": "dahai", "actor": 0, "pai": "E", "tsumogiri": False})
    print(f"dahai(0, E) → {r}")
    
    # 其他玩家操作
    r = send(proc, {"type": "tsumo", "actor": 1, "pai": "?"})
    print(f"tsumo(1) → {r}")
    r = send(proc, {"type": "dahai", "actor": 1, "pai": "N", "tsumogiri": True})
    print(f"dahai(1, N) → {r}")
    
    r = send(proc, {"type": "tsumo", "actor": 2, "pai": "?"})
    print(f"tsumo(2) → {r}")
    r = send(proc, {"type": "dahai", "actor": 2, "pai": "C", "tsumogiri": False})
    print(f"dahai(2, C) → {r}")
    
    r = send(proc, {"type": "tsumo", "actor": 3, "pai": "?"})
    print(f"tsumo(3) → {r}")
    r = send(proc, {"type": "dahai", "actor": 3, "pai": "P", "tsumogiri": True})
    print(f"dahai(3, P) → {r}")
    
    # 再摸一张牌
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "6m"})
    print(f"\ntsumo(0, 6m) → {r}")
    if r and r.get("type") == "dahai":
        print(f"  Mortal 第二次决策: {r['pai']}")
        # 手牌应该是: 1m 2m 3m 4m 5m 1p 2p 3p 1s 2s 3s S W + draw 6m
        # (打了 E, 手牌里不应该有 E)
        # Mortal 应该从 {1m,2m,3m,4m,5m,6m,1p,2p,3p,1s,2s,3s,S,W} 中选
        expected_hand = {"1m","2m","3m","4m","5m","6m","1p","2p","3p","1s","2s","3s","S","W"}
        pai = r['pai']
        if pai in expected_hand:
            print(f"  ✅ 出牌 {pai} 在预期手牌中")
        else:
            print(f"  ❌ 出牌 {pai} 不在预期手牌中! 预期: {expected_hand}")
    
    proc.stdin.close()
    proc.wait(timeout=5)

def test_replay_reach():
    """测试：重放时 Mortal 对 tsumo 返回 reach 怎么办"""
    proc = start_mortal(0)
    
    # 构造一手听牌
    send(proc, {"type": "start_game"})
    
    # 门清听牌手牌
    tehais = [
        ["1m","2m","3m","4p","5p","6p","7s","8s","9s","E","E","E","N"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
    ]
    send(proc, {
        "type": "start_kyoku",
        "bakaze": "E", "dora_marker": "1s", "kyoku": 1, "honba": 0, "kyotaku": 0,
        "oya": 0, "scores": [25000,25000,25000,25000], "tehais": tehais,
    })
    
    # 摸牌形成听牌，看 Mortal 会不会立直
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "N"})
    print(f"\n=== reach test ===")
    print(f"tsumo(0, N) → {r}")
    
    if r and r.get("type") == "reach":
        print("  Mortal 想立直!")
        # 重放时我们不需要立直，直接发 dahai
        # 但 Mortal 可能在等 reach 事件...
        # 试试直接发 dahai
        r2 = send(proc, {"type": "dahai", "actor": 0, "pai": "N", "tsumogiri": True})
        print(f"  直接发 dahai(N) → {r2}")
        
        # 看后续能否继续
        r3 = send(proc, {"type": "tsumo", "actor": 1, "pai": "?"})
        print(f"  tsumo(1) → {r3}")
        r4 = send(proc, {"type": "dahai", "actor": 1, "pai": "W", "tsumogiri": True})
        print(f"  dahai(1, W) → {r4}")
    elif r and r.get("type") == "dahai":
        print(f"  Mortal 不立直，打: {r['pai']}")
    
    proc.stdin.close()
    proc.wait(timeout=5)

if __name__ == "__main__":
    test_replay()
    test_replay_reach()
