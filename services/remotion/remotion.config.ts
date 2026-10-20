import {Config} from '@remotion/cli/config';

Config.setOverwriteOutput(true);
// Docker内Chromiumの安定化（GPUなし環境向け）
Config.setChromiumOpenGlRenderer('angle');
Config.setChromiumDisableWebSecurity(false);
