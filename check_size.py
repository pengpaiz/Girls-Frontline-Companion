import sys, os
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt, QSize

app = QApplication(sys.argv)

for name in ('click', 'wait', 'pick'):
    folder = f'data/an94/{name}'
    files = sorted(os.listdir(folder))
    f = files[0]
    p = QPixmap(os.path.join(folder, f))
    scaled = p.scaled(QSize(533, 400), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    print(f'{name}: orig={p.width()}x{p.height()} scaled={scaled.width()}x{scaled.height()}')

# Also check if all frames in click have same size
sizes = set()
for f in sorted(os.listdir('data/an94/click')):
    p = QPixmap(os.path.join('data/an94/click', f))
    s = p.scaled(QSize(533, 400), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    sizes.add((s.width(), s.height()))
print(f'\nclick animation: {len(sizes)} unique sizes')
for s in sorted(sizes):
    print(f'  {s[0]}x{s[1]}')