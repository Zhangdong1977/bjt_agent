const DEFAULT_OPERATE_PORT = '40060'
const DEFAULT_REGISTER_PROMOTER_CODE = '9yr4j0r1'

function cleanBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, '')
}

function operateHostFromCurrentHost(): string {
  return window.location.hostname.replace(/^check\./, '')
}

export function getOperateFrontendBaseUrl(): string {
  const configured = import.meta.env.VITE_OPERATE_FRONTEND_BASE_URL
  if (configured && configured.trim()) {
    return cleanBaseUrl(configured)
  }

  return `${window.location.protocol}//${operateHostFromCurrentHost()}:${DEFAULT_OPERATE_PORT}`
}

export function getOfficialSiteUrl(): string {
  const configured = import.meta.env.VITE_OFFICIAL_SITE_URL
  if (configured && configured.trim()) {
    return cleanBaseUrl(configured)
  }

  return `${window.location.protocol}//${operateHostFromCurrentHost()}/`
}

export function getRegisterUrl(): string {
  const promoterCode =
    import.meta.env.VITE_REGISTER_PROMOTER_CODE || DEFAULT_REGISTER_PROMOTER_CODE
  const url = new URL('/pluginLogin', getOperateFrontendBaseUrl())
  url.searchParams.set('promoterCode', promoterCode)
  url.searchParams.set('returnUrl', `${window.location.origin}/login`)
  return url.toString()
}
