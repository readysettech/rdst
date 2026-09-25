import fs from 'node:fs'
import path from 'node:path'

interface DesktopSettingsData {
  notificationsEnabled: boolean
  pausedTargets: string[]
  startAtLoginConfigured: boolean
  welcomeNotificationSent: boolean
}

export class DesktopSettings {
  private readonly filePath: string
  private data: DesktopSettingsData

  constructor(userDataPath: string) {
    this.filePath = path.join(userDataPath, 'desktop-settings.json')
    try {
      const saved = JSON.parse(fs.readFileSync(this.filePath, 'utf8')) as Partial<DesktopSettingsData>
      this.data = {
        notificationsEnabled: saved.notificationsEnabled !== false,
        pausedTargets: Array.isArray(saved.pausedTargets)
          ? saved.pausedTargets.filter((target): target is string => typeof target === 'string')
          : [],
        startAtLoginConfigured: saved.startAtLoginConfigured === true,
        welcomeNotificationSent: saved.welcomeNotificationSent === true,
      }
    } catch {
      this.data = {
        notificationsEnabled: true, pausedTargets: [], startAtLoginConfigured: false,
        welcomeNotificationSent: false,
      }
    }
  }

  get notificationsEnabled(): boolean {
    return this.data.notificationsEnabled
  }

  get pausedTargets(): string[] {
    return [...this.data.pausedTargets]
  }

  get startAtLoginConfigured(): boolean {
    return this.data.startAtLoginConfigured
  }

  get welcomeNotificationSent(): boolean {
    return this.data.welcomeNotificationSent
  }

  markWelcomeNotificationSent(): void {
    this.data.welcomeNotificationSent = true
    this.save()
  }

  markStartAtLoginConfigured(): void {
    this.data.startAtLoginConfigured = true
    this.save()
  }

  setNotificationsEnabled(enabled: boolean): void {
    this.data.notificationsEnabled = enabled
    this.save()
  }

  setPausedTargets(targets: string[]): void {
    this.data.pausedTargets = [...targets]
    this.save()
  }

  private save(): void {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true })
    const temporary = `${this.filePath}.tmp`
    fs.writeFileSync(temporary, `${JSON.stringify(this.data)}\n`, 'utf8')
    fs.renameSync(temporary, this.filePath)
  }
}
