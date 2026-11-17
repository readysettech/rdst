import { Button } from '@rs/ui-new/button'
import { useState } from 'react'
import { ReportAIOutputDialog } from './ReportAIOutputDialog'

interface ReportAIOutputButtonProps {
  output: string
  feature: string
  queryHash?: string
  label?: string
}

export function ReportAIOutputButton({
  output,
  feature,
  queryHash,
  label = 'Report AI output',
}: ReportAIOutputButtonProps) {
  const [open, setOpen] = useState(false)
  const reportableOutput = output.trim()

  if (!reportableOutput) return null

  return (
    <>
      <Button
        variant="primary"
        modifier="ghost"
        label={label}
        onClick={() => setOpen(true)}
      />
      {open ? (
        <ReportAIOutputDialog
          output={reportableOutput}
          feature={feature}
          queryHash={queryHash}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  )
}
