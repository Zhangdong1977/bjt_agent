import { ref, watch, onMounted } from 'vue'

type Theme = 'dark' | 'light'

const STORAGE_KEY = 'app-theme'

// 全局主题状态
const theme = ref<Theme>('dark')

// 系统偏好监听
const prefersDark = window.matchMedia('(prefers-color-scheme: dark)')

function handleSystemChange(e: MediaQueryListEvent) {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (!stored) {
    theme.value = e.matches ? 'dark' : 'light'
    applyTheme(theme.value)
  }
}

// 初始化主题
function initTheme() {
  const stored = localStorage.getItem(STORAGE_KEY) as Theme | null
  if (stored === 'dark' || stored === 'light') {
    theme.value = stored
  } else {
    // 无存储偏好时，使用系统偏好
    theme.value = prefersDark.matches ? 'dark' : 'light'
  }
  applyTheme(theme.value)

  // 监听系统主题变化
  prefersDark.addEventListener('change', handleSystemChange)
}

// 应用主题到 DOM
function applyTheme(t: Theme) {
  document.body.classList.remove('theme-dark', 'theme-light')
  document.body.classList.add(`theme-${t}`)
}

// 切换主题
function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
  localStorage.setItem(STORAGE_KEY, theme.value)
  applyTheme(theme.value)
}

// 设置特定主题
function setTheme(t: Theme) {
  theme.value = t
  localStorage.setItem(STORAGE_KEY, t)
  applyTheme(t)
}

export function useTheme() {
  onMounted(() => {
    initTheme()
  })

  watch(theme, (newTheme) => {
    applyTheme(newTheme)
  })

  return {
    theme,
    toggleTheme,
    setTheme,
    initTheme
  }
}