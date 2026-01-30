import { useState } from 'react'
import { Button } from '@rs/ui-new/button'
import { Text } from '@rs/ui-new/text'

interface SchemaExportButtonProps {
  onExport: (format: 'yaml' | 'json') => Promise<string | null>
  disabled?: boolean
}

export function SchemaExportButton({ onExport, disabled }: SchemaExportButtonProps) {
  const [isExporting, setIsExporting] = useState(false)
  const [isOpen, setIsOpen] = useState(false)

  const handleExport = async (format: 'yaml' | 'json') => {
    setIsOpen(false)
    setIsExporting(true)
    try {
      const content = await onExport(format)
      if (content) {
        const blob = new Blob([content], {
          type: format === 'yaml' ? 'text/yaml' : 'application/json',
        })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `semantic-layer.${format}`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        URL.revokeObjectURL(url)
      }
    } finally {
      setIsExporting(false)
    }
  }

  return (
    <div className="relative">
      <Button
        modifier="ghost"
        label={isExporting ? 'Exporting...' : 'Export'}
        onClick={() => setIsOpen(!isOpen)}
        loading={isExporting}
        disabled={disabled || isExporting}
      />
      {isOpen && (
        <>
          <button
            type="button"
            className="fixed inset-0 z-40 cursor-default"
            onClick={() => setIsOpen(false)}
            aria-label="Close menu"
          />
          <div className="absolute right-0 top-full mt-1 z-50 min-w-[120px] bg-surface-layout-2 border border-border-layout-1 rounded-lg shadow-lg overflow-hidden">
            <button
              type="button"
              onClick={() => handleExport('yaml')}
              className="w-full px-4 py-2 text-left hover:bg-surface-layout-3 transition-colors"
            >
              <Text level="body-small" className="text-content-layout-1">
                YAML
              </Text>
            </button>
            <button
              type="button"
              onClick={() => handleExport('json')}
              className="w-full px-4 py-2 text-left hover:bg-surface-layout-3 transition-colors"
            >
              <Text level="body-small" className="text-content-layout-1">
                JSON
              </Text>
            </button>
          </div>
        </>
      )}
    </div>
  )
}
