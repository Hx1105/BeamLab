# 实验台组件

BeamLab 实现数值光路、仪器模型、寻优操作、设备适配和实验工作台。[项目许可](../LICENSE)。

LightPipes 用于标量波动光学传播，NumPy/SciPy 用于数值计算，Matplotlib/Pillow 用于实验图像。随前端分发的 Three.js 资源保留 MIT 许可。

AGH、MHS 插件和 Python SDK 从仓库外的兼容安装接入。`scripts/attach_instruments.py` 生成本地依赖连接，不将官方源码作为项目实现的一部分。
