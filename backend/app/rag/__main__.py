import sys

from app.rag.pipeline import ask

if len(sys.argv) < 2:
    print('Cách dùng: python -m app.rag "Nội dung câu hỏi của bạn" [dense|hybrid]')
    sys.exit(1)

mode_arg = "hybrid"
args = sys.argv[1:]
if args[-1].lower() in ("dense", "hybrid"):
    mode_arg = args[-1].lower()
    args = args[:-1]
ask(" ".join(args), top_k=5, mode=mode_arg)
