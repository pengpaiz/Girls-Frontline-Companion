# AuraPet

Windows 桌面宠物软件，支持 PNG 序列帧动画播放。

## 功能
- 🎬 PNG 序列帧循环播放
- 🖱 鼠标拖拽移动位置
- ⚙ 图形化设置界面
  - 开机自启
  - 选择角色（data/ 下的角色文件夹）
  - 置顶开关
- 🔔 系统托盘图标，右键菜单（设置 / 退出）

## 运行

```bash
pip install -r requirements.txt
python main.py
```

## 自定义角色

在 `data/` 下新建文件夹，放入 PNG 序列帧，设置界面中即可选择。

## 项目结构

```
AuraPet/
├── main.py          # 主程序（PyQt5 桌面宠物）
├── config.py        # 配置管理模块
├── settings_ui.py   # 设置界面（CustomTkinter）
├── logo.ico         # 默认图标
├── data/            # 角色素材
│   └── an94_images/ # AN-94 待机动画（54 帧）
└── requirements.txt
```