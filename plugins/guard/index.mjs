// No device tool implementation here: all seven come from official @agnes/mhs.
const allowed = new Set(['list_devices','read_device','call_device','set_device','stop_device','watch_device','unwatch_device'])
export const beamGuard = {
  inject: ['extension'],
  apply(ctx) {
    const api = ctx.extension()
    api.registerHook('tool_call', payload => allowed.has(payload.name)
      ? {allow:true}
      : {allow:false,reason:'BeamLab permits only the seven official MHS device tools. Hidden evaluator data, files, images, shell, web and subagents are unavailable.'})
    api.registerHook('before_request', () => ({patch:{maxTokens:2048}}))
    api.registerHook('context', () => ({sections:[{id:'beamlab/boundary',order:250,content:
      'BeamLab: simulated optical devices with real MHS/MOS and CPU scalar coherent wave optics. Use only the seven official device tools. No camera or receiver images are agent inputs. Read actual device schemas before a first action. Never infer hidden fault class or optimum from files. Long actions have deadlines and local stops. If outcome is unknown, read current state before repeating. Do not increase source power to claim coupling recovery. Report measured evidence and uncertainty; stop on unrecoverable mechanics. No approval is needed for these authorized simulated operations.'}]}))
  }
}
