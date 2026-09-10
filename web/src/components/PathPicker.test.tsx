import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'

const HOME_LISTING = {
  current: '/Users/dana',
  parent: '/Users',
  directories: [{ name: 'projects', path: '/Users/dana/projects' }],
  files: [],
  is_home: true,
}

let browse: {
  data: typeof HOME_LISTING | undefined
  isLoading: boolean
  isError: boolean
}

vi.mock('../lib/useBrowse', () => ({ useBrowse: () => browse }))

import { PathPicker } from './PathPicker'

beforeAll(() => {
  const proto = HTMLElement.prototype as unknown as {
    hasPointerCapture: () => boolean
    setPointerCapture: () => void
    releasePointerCapture: () => void
    scrollIntoView: () => void
  }
  proto.hasPointerCapture = () => false
  proto.setPointerCapture = () => {}
  proto.releasePointerCapture = () => {}
  proto.scrollIntoView = () => {}
})

beforeEach(() => {
  browse = { data: undefined, isLoading: false, isError: false }
})

afterEach(cleanup)

describe('PathPicker', () => {
  it('accepts a typed or pasted path and keeps browsing as the second way in', () => {
    const onChange = vi.fn()
    render(<PathPicker value="" onChange={onChange} />)

    // The field was a button styled like an input, so a path could not be
    // typed or pasted at all. [E-16]
    const input = screen.getByLabelText('Directory path')
    expect(input.tagName).toBe('INPUT')
    fireEvent.change(input, { target: { value: '/srv/checkout' } })
    expect(onChange).toHaveBeenCalledWith('/srv/checkout')

    expect(screen.getByRole('button', { name: 'Browse' })).toBeTruthy()
  })

  it('labels the field for the kind of path it collects', () => {
    render(<PathPicker value="" onChange={() => {}} fileExt="sql" />)

    expect(screen.getByLabelText('File path')).toBeTruthy()
  })

  it('refuses to hand over the home directory in one click', () => {
    browse = { data: HOME_LISTING, isLoading: false, isError: false }
    render(<PathPicker value="" onChange={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Browse' }))
    const select = screen.getByRole('button', {
      name: 'Select this folder',
    }) as HTMLButtonElement
    expect(select.disabled).toBe(true)
    expect(screen.getByText(/Open the project you want to scan/i)).toBeTruthy()
  })

  it('selects a folder the reader walked into', () => {
    browse = {
      data: {
        ...HOME_LISTING,
        current: '/Users/dana/projects',
        is_home: false,
      },
      isLoading: false,
      isError: false,
    }
    const onChange = vi.fn()
    render(<PathPicker value="" onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Browse' }))
    fireEvent.click(screen.getByRole('button', { name: 'Select this folder' }))
    expect(onChange).toHaveBeenCalledWith('/Users/dana/projects')
  })
})
