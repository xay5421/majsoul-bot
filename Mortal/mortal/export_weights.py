#!/usr/bin/env python3
"""
把训练好的 state 文件导出为 bot 可用的精简权重文件

用法:
  python export_weights.py train_state.pth exported.pth
  python export_weights.py best_state.pth exported.pth

导出的文件只包含 mortal + dqn 权重 + config，体积更小
"""

import sys
import torch

def export(input_path, output_path):
    state = torch.load(input_path, weights_only=True, map_location='cpu')
    
    exported = {
        'config': state['config'],
        'mortal': state['mortal'],
        'current_dqn': state['current_dqn'],
    }
    
    torch.save(exported, output_path)
    
    in_size = os.path.getsize(input_path) / 1024 / 1024
    out_size = os.path.getsize(output_path) / 1024 / 1024
    
    cfg = state['config']
    print(f"导出完成:")
    print(f"  版本: v{cfg['control']['version']}")
    print(f"  架构: conv_channels={cfg['resnet']['conv_channels']}, num_blocks={cfg['resnet']['num_blocks']}")
    print(f"  步数: {state.get('steps', '?')}")
    print(f"  最佳成绩: {state.get('best_perf', '?')}")
    print(f"  输入: {input_path} ({in_size:.1f}MB)")
    print(f"  输出: {output_path} ({out_size:.1f}MB)")
    print(f"\n使用方法: 把 {output_path} 复制为 mortal.pth，更新 config.toml 中的 resnet 参数")

import os

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f"用法: {sys.argv[0]} <input.pth> <output.pth>")
        sys.exit(1)
    export(sys.argv[1], sys.argv[2])
