"""Keep the Docker service lifetime tied to its only operational API child."""
import hashlib


def patch(source):
    if hashlib.sha256(source.encode()).hexdigest() != '00b77b479ce19760cb0a1b290f5bb0e0c3b3354f6190e3b9be9130591cfba320':
        raise ValueError('unrecognized_core_release')
    old = 'child.on("exit",()=>process.exitCode=1).on("exit",()=>server.close());'
    new = '''let stopping=false;
child.once("exit",(code,signal)=>{
  console.error(JSON.stringify({event:"operational_child_exit",code,signal,stopping}));
  process.exit(stopping?0:1);
});'''
    stop = 'process.on("SIGTERM",()=>server.close(()=>child.kill("SIGTERM")));'
    if source.count(old) != 1 or source.count(stop) != 1:
        raise ValueError('unexpected_lifecycle')
    return source.replace(old,new).replace(stop, 'process.on("SIGTERM",()=>{stopping=true;server.close();child.kill("SIGTERM");});')
