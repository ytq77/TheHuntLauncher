"""Chinese text for the pinned 1.9.5 PC menu. IDs, values and game code are unchanged."""
ZH = {
    'PC Graphics': 'PC 画面', 'PC Effects': 'PC 特效', 'PC Options': 'PC 设置',
    'Upscaling, frame generation, resolution and frame rate.': '设置超分辨率、zhen 生成、分辨率与 zhen 率。',
    'Model detail, shadows, reflections and post-processing effects.': '设置模型细节、阴影、反射与后期效果。',
    'Camera, HUD, button prompts and other PC options.': '设置镜头、界面、按键提示及其他 PC 选项。',
    'Upscaler': '超分辨率', 'Upscaler Quality': '超分画质', 'Sharpness': '锐化',
    'Frame Generation': 'zhen 生成', 'Generated Frames': 'zhen 生成倍数',
    'Output Resolution': '输出分辨率', 'Frame Rate': 'zhen 率', 'Frame Cap': 'zhen 率上限',
    'Model Detail': '模型细节', 'Ambient Occlusion': '环境光遮蔽',
    'Dynamic Shadows': '动态阴影', 'Reflections': '反射', 'Anti-Aliasing': '抗锯齿',
    'Motion Blur': '动态模糊', 'Depth of Field': '景深', 'Chromatic Aberration': '色差',
    'Field of View': '视野范围', 'Camera Distance': '镜头距离',
    'Auto Camera Rotation': '自动旋转镜头', 'HUD Position': '界面位置',
    'FPS Counter': 'zhen 率显示', 'Button Prompts': '按键提示',
    'Mute in Background': '后台静音', 'Skip Intro': '跳过开场', 'Restart Game': '重启游戏',
    'Smooths edges and rebuilds detail at the output resolution.': '平滑边缘，并重建输出分辨率下的画面细节。',
    'Scene resolution: Native is sharpest, Performance is fastest.': '内部画面分辨率：原生最清晰，性能模式最快。',
    'Sharpens the upscaled image.': '调整超分辨率画面的锐化程度。',
    "Adds generated frames between the game's frames.": '在游戏原有的 zhen 之间生成额外 zhen。',
    'Frames shown per game frame (DLSS on RTX 50 up to 4x).': '每个游戏 zhen 对应的显示 zhen 数；RTX 50 系列的 DLSS 最高支持 4 倍。',
    'Final image size. Ultrawide sizes apply after restart.': '最终输出分辨率；超宽屏尺寸需重启游戏后生效。',
    "The game's frame rate. Applies after restart.": '游戏实际运行 zhen 率；重启游戏后生效。',
    'Limit of the unlocked frame rate. Applies after restart.': '解除 zhen 率锁定后的上限；重启游戏后生效。',
    'Detail of distant models. Applies after restart.': '远处模型的细节程度；重启游戏后生效。',
    'Contact shadows in corners. Applies after restart.': '物体接触处及角落的阴影；重启游戏后生效。',
    'Shadows of moving lights. Applies after restart.': '移动光源产生的阴影；重启游戏后生效。',
    'Screen-space reflections on wet ground. Applies after restart.': '潮湿地面的屏幕空间反射；重启游戏后生效。',
    "The game's own edge smoothing. Applies after restart.": '游戏原有的边缘平滑效果；重启游戏后生效。',
    'Blurs fast movement. Applies after restart.': '快速移动时的模糊效果；重启游戏后生效。',
    'Blurs what is out of focus. Applies after restart.': '焦点之外的模糊效果；重启游戏后生效。',
    'Colour fringes at the screen edges. Applies after restart.': '画面边缘的彩色偏移效果；重启游戏后生效。',
    'Camera field of view in degrees. Applies after restart.': '镜头视野角度；重启游戏后生效。',
    'Places the camera further back. Applies after restart.': '调整镜头与角色之间的距离；重启游戏后生效。',
    'Camera turns by itself while moving. Applies after restart.': '移动时自动转动镜头；重启游戏后生效。',
    'Ultrawide: moves the HUD toward the edges. Applies after restart.': '超宽屏界面向屏幕边缘移动的程度；重启游戏后生效。',
    'Shows the frame rate in a corner of the screen.': '在屏幕角落显示 zhen 率。',
    'Which buttons the game shows.': '选择游戏中显示的按键图标类型。',
    'Silences the game while another window is active.': '切换到其他窗口时静音。',
    'Skips the logos and opening movie. Applies after restart.': '跳过标志与开场影片；重启游戏后生效。',
    'Restarts the game now to apply the changes marked for restart.': '立即重启游戏，使注明需要重启的设置生效。',
    'Restarts the game to apply the changes marked for restart.': '重启游戏，使注明需要重启的设置生效。',
    'Restart the game now\nto apply the changes?': '要立即重启游戏\n使设置生效吗？',
    'Off': '关闭', 'Native': '原生', 'Quality': '画质', 'Balanced': '均衡',
    'Performance': '性能', 'Ultra Performance': '极致性能', 'Unlocked': '不锁 zhen',
    'Auto (display, max 120)': '自动（显示器，最高 120）', 'No limit': '不限制',
    '43 (default)': '43（默认）', 'Highest': '最高', 'High': '高', 'Default': '默认',
    'Low': '低', 'Lowest': '最低', '100% (edges)': '100%（屏幕边缘）',
    'Auto': '自动', 'Keyboard & Mouse': '键盘与鼠标', 'Restart Now': '立即重启',
}


def localized(text):
    return ZH.get(text, text)


def compatible_existing(text):
    """Retain existing PC translations while replacing the two observed missing glyphs."""
    labels = {'帧生成': 'zhen 生成', '帧生成倍数': 'zhen 生成倍数',
              '帧率': 'zhen 率', '帧率上限': 'zhen 率上限',
              '帧率显示': 'zhen 率显示', '不锁帧': '不锁 zhen'}
    return labels.get(text, text.replace('帧', 'zhen').replace('渲染', '输出').replace('渲', '画'))
