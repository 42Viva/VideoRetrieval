# VideoRetrieval基于文字、图像的视频检索、图像匹配

[**中文**](./README.md) | [**English**](./README_EN.md)

扫描本地的图片以及视频，使用自然语言进行检索。

## 功能

- 文字搜图
- 以图搜图
- 文字搜视频（会给出符合描述的视频片段）
- 以图搜视频（通过视频截图搜索所在片段）
- 图文相似度计算（只是给出一个分数，仅作参考）

## 使用说明

### 通过源码部署

注意，首次运行会自动下载模型。下载速度可能比较慢，请耐心等待。如果网络不好，模型可能会下载失败，这个时候重新执行程序即可。

1. 首次使用前需要安装依赖：`pip install -r requirements.txt`，Windows系统可以双击`install.bat`。如果你用Wi
2. ndows且打算使用GPU加速，请根据[官方文档](https://pytorch.org/get-started/locally/)手动安装torch。`install.bat`只会安装仅支持CPU的torch。
3. 如果你打算使用GPU加速，则执行基准测试判断是CPU快还是GPU快：`python benchmark.py`，Windows系统可以双击`benchmark.bat`。GPU不一定比CPU快，在我的Mac上CPU更快。
4. 如果不是CPU最快，则修改`config.py`中的`DEVICE`和`DEVICE_TEXT`，改为对应设备，如`DEVICE = "cuda"`。
5. 启动程序：`python main.py`，Windows系统可以双击`run.bat`。

如遇到`requirements.txt`版本依赖问题（比如某个库版本过新会导致运行报错），请提issue反馈，我会添加版本范围限制。


## 配置说明

所有配置都在`config.py`文件中，里面已经写了详细的注释。

如果你发现某些格式的图片或视频没有被扫描到，可以尝试在`IMAGE_EXTENSIONS`和`VIDEO_EXTENSIONS`增加对应的后缀。如果你发现一些支持的后缀没有被添加到代码中，欢迎提issue或pr增加。

小图片没被扫描到的话，可以调低`IMAGE_MIN_WIDTH`和`IMAGE_MIN_HEIGHT`重试。

## 搜索速度

在 J3455 CPU 上，语言为English，1秒钟可以进行大约5000-8300次匹配。目前只能用到单核，计划后续优化。

## 已知问题

1. 部分图片和视频无法在网页上显示，原因是浏览器不支持这一类型的文件（例如tiff文件，svq3编码的视频等）。
2. 暂时无法通过多进程优化查询速度。

## 关于PR

欢迎提PR！不过为了避免无意义的劳动，建议先提issue讨论一下。

提PR前请确保代码已经格式化。

## 特别鸣谢
本项目受[MaterialSearch](https://github.com/chn-lee-yumi/MaterialSearch)启发，因考虑到数据量大，改用MongoDB数据库，加快向量检索匹配速度。
