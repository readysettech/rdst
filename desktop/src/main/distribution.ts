import distributions from '../../distributions.json' with { type: 'json' }

// The channels RDST Desktop ships through and the rules each one sets: whether diagnostics and
// AI wait for an opt-in, whether RDST asks before starting at sign-in, and whether reports may
// go to Readyset-hosted services. A packaged build carries its distribution's name (CI bakes
// it in as rdstDistribution); a build without one, and any unpackaged run, is the direct
// download.

export type DistributionName = keyof typeof distributions
export type Distribution = (typeof distributions)[DistributionName] & {
  name: DistributionName
}

const DIRECT: DistributionName = 'direct'

const isDistributionName = (name: string): name is DistributionName =>
  Object.hasOwn(distributions, name)

/** The rules of the distribution named `name`; one distributions.json doesn't list is a build error. */
export function distributionRules(name: string | undefined): Distribution {
  const key = name || DIRECT
  if (!isDistributionName(key))
    throw new Error(`Unknown RDST Desktop distribution: ${key}`)
  return { name: key, ...distributions[key] }
}
