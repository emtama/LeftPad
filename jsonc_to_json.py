import sys
import re
import json

def convert_jsonc_to_json(file_path):
    if not file_path.endswith('.jsonc'):
        return
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # コメントの除去 (/* ... */ および // ...)
        content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
        content = re.sub(r'//.*', '', content)
        
        # JSONとしてパース（構文チェック）
        data = json.loads(content)
        
        # 拡張子を .json に変えて保存
        output_path = file_path.rsplit('.', 1)[0] + '.json'
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        print(f"Successfully converted: {output_path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        convert_jsonc_to_json(sys.argv[1])