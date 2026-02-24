"""测试 Mortal reach 处理方案"""
import subprocess
import json

MORTAL_DIR = "/home/ubuntu/workspace/Mortal/mortal"
PYTHON = "/home/ubuntu/workspace/majsoul-bot/.venv/bin/python"

def start_mortal(seat=0):
    proc = subprocess.Popen(
        [PYTHON, "mortal.py", str(seat)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=MORTAL_DIR, text=True,
    )
    return proc

def send(proc, event):
    line = json.dumps(event)
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    resp = proc.stdout.readline().strip()
    return json.loads(resp) if resp else None

def test_b():
    """方案B: reach 后按协议走"""
    proc = start_mortal(0)
    send(proc, {"type": "start_game"})
    
    tehais = [
        ["1m","2m","3m","4m","5m","1p","2p","3p","1s","2s","3s","E","S"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
        ["?","?","?","?","?","?","?","?","?","?","?","?","?"],
    ]
    send(proc, {
        "type": "start_kyoku",
        "bakaze": "E", "dora_marker": "1s", "kyoku": 1, "honba": 0, "kyotaku": 0,
        "oya": 0, "scores": [25000,25000,25000,25000], "tehais": tehais,
    })
    
    # 巡1
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "W"})
    print(f"巡1 tsumo(W) → type={r['type']}, pai={r.get('pai','')}")
    send(proc, {"type": "dahai", "actor": 0, "pai": "E", "tsumogiri": False})
    for i in range(1,4):
        send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
        send(proc, {"type": "dahai", "actor": i, "pai": "N", "tsumogiri": True})
    
    # 巡2: Mortal 可能要 reach
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "6m"})
    print(f"巡2 tsumo(6m) → type={r['type']}")
    
    if r['type'] == 'reach':
        print("  Mortal 想立直！按协议走 reach → dahai")
        # 发 reach 事件获取 dahai
        r_reach = send(proc, {"type": "reach", "actor": 0})
        print(f"  reach → {r_reach}")
        # Mortal 想打的牌
        mortal_pai = r_reach.get('pai', '?') if r_reach else '?'
        print(f"  Mortal 立直要打: {mortal_pai}")
        
        # 实际重放中可能打了不同的牌（且不立直）
        # 发实际的 dahai
        r_dahai = send(proc, {"type": "dahai", "actor": 0, "pai": "S", "tsumogiri": False})
        print(f"  发实际 dahai(S) → {r_dahai}")
        
        # reach_accepted（因为历史上可能立直也可能没有）
        # 如果历史上没立直，不发 reach_accepted
        # 如果历史上立直了，发 reach_accepted
        # 但 Mortal 已经 reach 了... 不发 reach_accepted 行不行？
        
        # 试试不发 reach_accepted 继续
        print("  不发 reach_accepted，继续...")
    else:
        send(proc, {"type": "dahai", "actor": 0, "pai": "S", "tsumogiri": False})
    
    try:
        for i in range(1,4):
            send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
            send(proc, {"type": "dahai", "actor": i, "pai": "N", "tsumogiri": True})
        
        r = send(proc, {"type": "tsumo", "actor": 0, "pai": "7m"})
        print(f"巡3 tsumo(7m) → type={r['type']}, pai={r.get('pai','')}")
        
        if r and r['type'] == 'dahai':
            expected = {"1m","2m","3m","4m","5m","6m","7m","1p","2p","3p","1s","2s","3s","W"}
            pai = r['pai']
            status = "✅" if pai in expected else "❌"
            print(f"  {status} 出牌 {pai} {'在' if pai in expected else '不在'}预期手牌中")
        
    except (BrokenPipeError, Exception) as e:
        print(f"  ❌ 后续操作失败: {e}")
        err = proc.stderr.read()
        if err:
            print(f"  stderr: {err[:500]}")
    
    proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except:
        proc.kill()

    # 方案C: reach → dahai → reach_accepted 完整走完
    print("\n=== 方案C: 完整 reach 协议 ===")
    proc = start_mortal(0)
    send(proc, {"type": "start_game"})
    send(proc, {
        "type": "start_kyoku",
        "bakaze": "E", "dora_marker": "1s", "kyoku": 1, "honba": 0, "kyotaku": 0,
        "oya": 0, "scores": [25000,25000,25000,25000], "tehais": tehais,
    })
    
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "W"})
    send(proc, {"type": "dahai", "actor": 0, "pai": "E", "tsumogiri": False})
    for i in range(1,4):
        send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
        send(proc, {"type": "dahai", "actor": i, "pai": "N", "tsumogiri": True})
    
    r = send(proc, {"type": "tsumo", "actor": 0, "pai": "6m"})
    print(f"巡2 tsumo(6m) → type={r['type']}")
    
    if r['type'] == 'reach':
        r2 = send(proc, {"type": "reach", "actor": 0})
        mortal_pai = r2.get('pai', '?') if r2 else '?'
        print(f"  reach → dahai: {mortal_pai}")
        
        # 发实际 dahai（打了 Mortal 建议的牌）
        r3 = send(proc, {"type": "dahai", "actor": 0, "pai": mortal_pai, "tsumogiri": False})
        print(f"  dahai({mortal_pai}) → {r3}")
        
        # reach_accepted
        r4 = send(proc, {"type": "reach_accepted", "actor": 0})
        print(f"  reach_accepted → {r4}")
    
    try:
        for i in range(1,4):
            send(proc, {"type": "tsumo", "actor": i, "pai": "?"})
            send(proc, {"type": "dahai", "actor": i, "pai": "N", "tsumogiri": True})
        
        # 立直后摸牌
        r = send(proc, {"type": "tsumo", "actor": 0, "pai": "7m"})
        print(f"巡3 tsumo(7m) → type={r['type']}, pai={r.get('pai','')}")
        
    except (BrokenPipeError, Exception) as e:
        print(f"  ❌ 后续失败: {e}")
        err = proc.stderr.read()
        if err:
            print(f"  stderr: {err[:300]}")
    
    proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except:
        proc.kill()

test_b()
