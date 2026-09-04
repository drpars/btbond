"""`python -m btbond` — kurulum yokken de aynı ön kapı.

`runner.self_command()` kurulu giriş noktasını bulamazsa buraya düşüyor, yani
depodan koşulan bir TUI'nin başlattığı alt süreçler de bu yoldan geçiyor.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
