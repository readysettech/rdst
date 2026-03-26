/**
 * Shared Cache button used across Top Queries, Query Registry, Scan Results.
 * Shows: Cache → loading → Cached (disabled).
 */

import { Button } from '@rs/ui-new/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@rs/ui-new/tooltip';

interface CacheButtonProps {
  /** Is this query already cached this session? */
  cached: boolean;
  /** Is this query currently being cached? */
  loading: boolean;
  /** Cache button click handler. */
  onClick: () => void;
  /** Button size. */
  size?: 'small' | 'base';
}

export function CacheButton({ cached, loading, onClick, size = 'small' }: CacheButtonProps) {
  if (cached) {
    return (
      <Button
        variant="primary"
        modifier="ghost"
        size={size}
        icon="tick-double"
        iconPosition="left"
        label="Cached"
        disabled
      />
    );
  }

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div>
            <Button
              variant="primary"
              modifier="ghost"
              size={size}
              icon="database-settings"
              iconPosition="left"
              label="Cache"
              loading={loading}
              onClick={onClick}
            />
          </div>
        </TooltipTrigger>
        <TooltipContent label="Cache this query with ReadySet for sub-ms reads" />
      </Tooltip>
    </TooltipProvider>
  );
}
