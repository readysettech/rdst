import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchSshDirectory,
  fetchSshKeys,
  fetchSshProfiles,
} from '../../lib/tunnels'
import {
  assembleSshConfig,
  SshFields,
  type SshFieldsValue,
  sshFieldsValue,
} from './SshFields'

vi.mock('../../lib/tunnels', () => ({
  fetchSshDirectory: vi.fn(),
  fetchSshKeys: vi.fn(),
  fetchSshProfiles: vi.fn(),
  importSshKey: vi.fn(),
}))

function Harness({
  onSubmit,
  initialValue = sshFieldsValue(),
}: {
  onSubmit: (value: ReturnType<typeof assembleSshConfig>) => void
  initialValue?: SshFieldsValue
}) {
  const [value, setValue] = useState<SshFieldsValue>(initialValue)
  return (
    <>
      <SshFields value={value} onChange={setValue} />
      <button type="button" onClick={() => onSubmit(assembleSshConfig(value))}>
        Assemble
      </button>
    </>
  )
}

describe('SshFields', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Element.prototype.scrollIntoView = vi.fn()
    vi.mocked(fetchSshDirectory).mockResolvedValue({
      path: '/home/test',
      parent: null,
      entries: [],
    })
    vi.mocked(fetchSshKeys).mockResolvedValue([])
    vi.mocked(fetchSshProfiles).mockResolvedValue([])
  })

  afterEach(() => {
    cleanup()
    delete window.rdstDesktop
  })

  it('assembles trimmed fields with SSH port 22 by default', () => {
    const onSubmit = vi.fn()
    render(<Harness onSubmit={onSubmit} />)

    fireEvent.change(screen.getByLabelText('Jump host'), {
      target: { value: ' bastion.example.com ' },
    })
    fireEvent.change(screen.getByLabelText('SSH user'), {
      target: { value: ' ec2-user ' },
    })
    fireEvent.click(screen.getByRole('combobox', { name: 'Key path' }))
    fireEvent.click(screen.getByRole('option', { name: 'Enter path manually' }))
    fireEvent.change(screen.getByLabelText('Key path'), {
      target: { value: ' ~/.ssh/prod.pem ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Assemble' }))

    expect(onSubmit).toHaveBeenCalledWith({
      host: 'bastion.example.com',
      port: 22,
      user: 'ec2-user',
      key_path: '~/.ssh/prod.pem',
    })
  })

  it('assembles a blank jump host as no SSH configuration', () => {
    expect(
      assembleSshConfig({
        profile: '',
        host: '',
        port: 22,
        user: '',
        key_path: '',
      })
    ).toBeUndefined()
  })

  it('selects a discovered key without leaving detected-key mode', async () => {
    const onSubmit = vi.fn()
    vi.mocked(fetchSshKeys).mockResolvedValue([
      {
        kind: 'file',
        label: '~/.ssh: id_ed25519',
        key_path: '/home/test/.ssh/id_ed25519',
      },
    ])
    render(<Harness onSubmit={onSubmit} />)

    await waitFor(() => expect(fetchSshKeys).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('combobox', { name: 'Key path' }))
    fireEvent.click(
      await screen.findByRole('option', { name: '~/.ssh: id_ed25519' })
    )
    fireEvent.change(screen.getByLabelText('Jump host'), {
      target: { value: 'bastion.example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Assemble' }))

    expect(onSubmit).toHaveBeenCalledWith({
      host: 'bastion.example.com',
      port: 22,
      key_path: '/home/test/.ssh/id_ed25519',
    })
  })

  it('browses backend files in the web runtime and selects a key', async () => {
    const onSubmit = vi.fn()
    vi.mocked(fetchSshDirectory)
      .mockResolvedValueOnce({
        path: '/home/test',
        parent: null,
        entries: [
          {
            name: '.ssh',
            path: '/home/test/.ssh',
            is_dir: true,
          },
        ],
      })
      .mockResolvedValueOnce({
        path: '/home/test/.ssh',
        parent: '/home/test',
        entries: [
          {
            name: 'id_ed25519',
            path: '/home/test/.ssh/id_ed25519',
            is_dir: false,
          },
        ],
      })
    render(<Harness onSubmit={onSubmit} />)

    fireEvent.click(screen.getByRole('combobox', { name: 'Key path' }))
    fireEvent.click(screen.getByRole('option', { name: 'Browse local files' }))
    fireEvent.click(
      await screen.findByRole('button', { name: 'Open folder: .ssh' })
    )
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Select file: id_ed25519',
      })
    )
    fireEvent.change(screen.getByLabelText('Jump host'), {
      target: { value: 'bastion.example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Assemble' }))

    expect(fetchSshDirectory).toHaveBeenCalledWith(undefined)
    expect(fetchSshDirectory).toHaveBeenCalledWith('/home/test/.ssh')
    expect(onSubmit).toHaveBeenCalledWith({
      host: 'bastion.example.com',
      port: 22,
      key_path: '/home/test/.ssh/id_ed25519',
    })
  })

  it('uses the native picker in the desktop runtime', async () => {
    const onSubmit = vi.fn()
    const selectSshKey = vi
      .fn()
      .mockResolvedValue('C:\\Users\\me\\.ssh\\id_ed25519')
    window.rdstDesktop = {
      isDesktop: true,
      platform: 'win32',
      files: { selectSshKey },
    }
    render(<Harness onSubmit={onSubmit} />)

    expect(
      screen.queryByRole('option', { name: 'Browse local files' })
    ).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Browse...' }))
    await waitFor(() => expect(selectSshKey).toHaveBeenCalledOnce())
    fireEvent.change(screen.getByLabelText('Jump host'), {
      target: { value: 'bastion.example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Assemble' }))

    expect(fetchSshDirectory).not.toHaveBeenCalled()
    expect(onSubmit).toHaveBeenCalledWith({
      host: 'bastion.example.com',
      port: 22,
      key_path: 'C:\\Users\\me\\.ssh\\id_ed25519',
    })
  })

  it('assembles reusable profiles without duplicating inline settings', () => {
    expect(
      assembleSshConfig({
        profile: 'production-bastion',
        host: 'ignored.example.com',
        port: 2222,
        user: 'ignored',
        key_path: '/ignored',
      })
    ).toEqual({ profile: 'production-bastion' })
  })

  it('round-trips an untouched profile reference without expanding it', async () => {
    const onSubmit = vi.fn()
    vi.mocked(fetchSshProfiles).mockResolvedValue([
      {
        name: 'production-bastion',
        host: 'bastion.prod.test',
        port: 2222,
        user: 'ops',
        key_path: '/keys/prod',
      },
    ])

    render(
      <Harness
        onSubmit={onSubmit}
        initialValue={sshFieldsValue({ profile: 'production-bastion' })}
      />
    )

    await waitFor(() => {
      expect(
        screen.getByRole('combobox', { name: 'Jump host' }).textContent
      ).toContain('bastion.prod.test')
    })
    fireEvent.click(screen.getByRole('button', { name: 'Assemble' }))

    expect(onSubmit).toHaveBeenCalledWith({ profile: 'production-bastion' })
  })

  it('expands a profile into inline settings after a field is edited', async () => {
    const onSubmit = vi.fn()
    vi.mocked(fetchSshProfiles).mockResolvedValue([
      {
        name: 'production-bastion',
        host: 'bastion.prod.test',
        port: 2222,
        user: 'ops',
        key_path: '/keys/prod',
      },
    ])

    render(
      <Harness
        onSubmit={onSubmit}
        initialValue={sshFieldsValue({ profile: 'production-bastion' })}
      />
    )

    const keySelect = await screen.findByRole('combobox', { name: 'Key path' })
    await waitFor(() => expect(keySelect.textContent).toContain('/keys/prod'))
    fireEvent.click(keySelect)
    fireEvent.click(screen.getByRole('option', { name: 'Enter path manually' }))
    fireEvent.change(screen.getByLabelText('Key path'), {
      target: { value: '/keys/new' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Assemble' }))

    expect(onSubmit).toHaveBeenCalledWith({
      host: 'bastion.prod.test',
      port: 2222,
      user: 'ops',
      key_path: '/keys/new',
    })
  })

  it('shows the current edit-mode jump host as the selected option', () => {
    render(
      <Harness
        onSubmit={vi.fn()}
        initialValue={{
          profile: '',
          host: 'bastion.current.test',
          port: 2222,
          user: 'ec2-user',
          key_path: '',
        }}
      />
    )

    expect(
      screen.getByRole('combobox', { name: 'Jump host' }).textContent
    ).toContain('bastion.current.test')
    expect(screen.queryByLabelText('SSH user')).toBeNull()
  })

  it('keeps a remembered host selected after switching in edit mode', async () => {
    vi.mocked(fetchSshProfiles).mockResolvedValue([
      {
        name: 'next-bastion',
        host: 'bastion.next.test',
        port: 22,
        user: 'ubuntu',
        key_path: null,
      },
    ])

    render(
      <Harness
        onSubmit={vi.fn()}
        initialValue={{
          profile: '',
          host: 'bastion.current.test',
          port: 2222,
          user: 'ec2-user',
          key_path: '',
        }}
      />
    )

    const select = await screen.findByRole('combobox', { name: 'Jump host' })
    fireEvent.click(select)
    fireEvent.click(
      await screen.findByRole('option', { name: 'bastion.next.test (ubuntu)' })
    )

    await waitFor(() => {
      const selected = screen.getByRole('combobox', { name: 'Jump host' })
      expect(selected.textContent).toContain('bastion.next.test')
      expect(selected.textContent).not.toBe('')
    })
  })
})
