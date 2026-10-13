import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  type Advanced,
  AdvancedFields,
  advancedConnection,
  advancedFrom,
  defaultPasswordEnv,
  nameError,
} from './ConfigureForm'

afterEach(cleanup)

function Harness({ initial }: { initial: Advanced }) {
  const [value, setValue] = useState(initial)
  return (
    <>
      <AdvancedFields
        value={value}
        engine={value.engine ?? 'postgresql'}
        onChange={setValue}
      />
      <output data-testid="value">
        {JSON.stringify(advancedConnection(value))}
      </output>
    </>
  )
}

const shown = () =>
  JSON.parse(screen.getByTestId('value').textContent ?? '{}') as Record<
    string,
    unknown
  >

describe('AdvancedFields', () => {
  it('stays collapsed until asked for, then shows engine, jump host and TLS', () => {
    render(<Harness initial={advancedFrom()} />)

    const toggle = screen.getByRole('button', { name: /Advanced/ })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(toggle)

    expect(screen.getByRole('combobox', { name: 'Engine' })).toBeTruthy()
    expect(screen.getByText('SSH jump host')).toBeTruthy()
    expect(screen.getByRole('switch', { name: 'TLS encryption' })).toBeTruthy()
    expect(
      screen.queryByRole('switch', { name: 'Verify TLS certificate' })
    ).toBeNull()
  })

  it('discovers SSH keys only after Advanced is opened', async () => {
    // Key discovery reads ~/Downloads, which macOS asks about; it must wait for the user.
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (url) =>
        Response.json(
          String(url).startsWith('/api/tunnel/ssh-keys') ? { options: [] } : []
        )
      )
    const keyRequests = () =>
      fetchMock.mock.calls.filter(([url]) =>
        String(url).startsWith('/api/tunnel/ssh-keys')
      )
    try {
      render(<Harness initial={advancedFrom()} />)
      await new Promise((resolve) => setTimeout(resolve, 300))
      expect(keyRequests()).toHaveLength(0)
      fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
      await waitFor(() => expect(keyRequests()).toHaveLength(1))
    } finally {
      fetchMock.mockRestore()
    }
  })

  it('reveals verification and the CA path under TLS, and clears them when TLS goes off', () => {
    render(<Harness initial={advancedFrom()} />)
    fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))

    fireEvent.click(screen.getByRole('switch', { name: 'TLS encryption' }))
    fireEvent.click(
      screen.getByRole('switch', { name: 'Verify TLS certificate' })
    )
    fireEvent.change(screen.getByLabelText('TLS CA path (optional)'), {
      target: { value: ' /ca.pem ' },
    })
    expect(shown()).toMatchObject({
      tls: true,
      tls_verify: true,
      tls_ca: '/ca.pem',
    })

    fireEvent.click(screen.getByRole('switch', { name: 'TLS encryption' }))
    expect(shown()).toMatchObject({ tls: false, tls_verify: false })
  })

  it('carries a typed jump host as the nested SSH configuration', () => {
    render(<Harness initial={advancedFrom()} />)
    fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))

    fireEvent.change(screen.getByLabelText('Jump host'), {
      target: { value: 'bastion.example.com' },
    })
    fireEvent.change(screen.getByLabelText('SSH user'), {
      target: { value: 'ec2-user' },
    })

    expect(shown().ssh).toEqual({
      host: 'bastion.example.com',
      port: 22,
      user: 'ec2-user',
    })
  })
})

describe('advancedFrom', () => {
  it('reads a saved database and leaves the engine to the form when none is saved', () => {
    expect(advancedFrom().engine).toBeNull()
    const saved = advancedFrom({
      engine: 'mysql',
      tls: true,
      tls_verify: true,
      tls_ca: '/ca.pem',
      ssh: { host: 'bastion', port: 2222 },
    })
    expect(saved).toMatchObject({
      engine: 'mysql',
      tls: true,
      tlsVerify: true,
      tlsCa: '/ca.pem',
    })
    expect(advancedConnection(saved).ssh).toEqual({
      host: 'bastion',
      port: 2222,
    })
  })
})

describe('nameError', () => {
  it('accepts an empty or a free name and refuses a taken one in any case', () => {
    expect(nameError('', ['shop'])).toBeNull()
    expect(nameError('storefront', ['shop'])).toBeNull()
    expect(nameError('SHOP', ['shop'])).toBe(
      'A database named SHOP already exists.'
    )
  })

  it('refuses names that can not be saved', () => {
    for (const name of ['my shop', '-shop', 'shop.', 'x'.repeat(65)])
      expect(nameError(name, [])).toBe(
        'Use up to 64 letters, numbers, dots, underscores or hyphens, starting with a letter or number.'
      )
  })
})

describe('defaultPasswordEnv', () => {
  it('derives the hidden password name from the database name', () => {
    expect(defaultPasswordEnv('ci-shop.2')).toBe('RDST_CI_SHOP_2_PASSWORD')
    expect(defaultPasswordEnv('  ')).toBe('')
  })
})
