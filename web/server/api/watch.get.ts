export default defineEventHandler(() => {
  const config = useRuntimeConfig()
  return {
    signature: projectsSignature(),
    dir: projectsDir(),
    core: coreCommand(),
    version: config.public.version,
    at: new Date().toISOString(),
  }
})
