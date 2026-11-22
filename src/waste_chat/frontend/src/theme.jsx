import '@astryxdesign/core/reset.css';
import '@astryxdesign/core/astryx.css';
import '@astryxdesign/theme-neutral/theme.css';

import { Theme } from '@astryxdesign/core/theme';
import { neutralTheme } from '@astryxdesign/theme-neutral/built';
import { InternationalizationProvider } from '@astryxdesign/core/i18n';
import koKR from '@astryxdesign/core/locales/ko-KR.generated.js';

const messages = { 'ko-KR': koKR };

export function UITheme({ children }) {
  return (
    <Theme theme={neutralTheme} mode="light">
      <InternationalizationProvider locale="ko-KR" messages={messages}>
        {children}
      </InternationalizationProvider>
    </Theme>
  );
}
