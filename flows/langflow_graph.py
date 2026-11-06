"""Build a model-free graph from the stock components already shipped in the pinned image."""
import copy
import json
import pathlib

def build(starter):
    nodes = {n["data"].get("type"): n for n in starter["data"]["nodes"]}
    inlet, prompt, outlet = [copy.deepcopy(nodes[t]) for t in ["ChatInput","Prompt","ChatOutput"]]
    cfg=prompt["data"]["node"]["template"]
    cfg["template"]["value"]="FAQ do piloto fictício: atendimento de segunda a sexta, das 09h às 17h. Categoria: {topic}"
    field=copy.deepcopy(outlet["data"]["node"]["template"]["input_value"])
    field.update(name="topic",display_name="topic",value="",input_types=["Message"],required=True)
    cfg["topic"]=field
    prompt["data"]["node"]["custom_fields"]={"template":["topic"]}
    for i,n in enumerate((inlet,prompt,outlet)):
        n["position"]={"x":300*i,"y":0}
        nt=n["data"]["node"]["template"]
        if "should_store_message" in nt: nt["should_store_message"]["value"]=False
    def edge(src, source_output, dst, dest_field):
        field=dst["data"]["node"]["template"][dest_field]
        sh={"id":src["id"],"dataType":src["data"]["type"],"name":source_output,"output_types":["Message"]}
        th={"id":dst["id"],"fieldName":dest_field,"inputTypes":field.get("input_types",["Message"]),"type":field.get("type","str")}
        encode=lambda x:json.dumps(x,ensure_ascii=False,separators=(",",":")).replace('"',"œ")
        return {"id":src["id"]+"-"+dst["id"],"source":src["id"],"target":dst["id"],"sourceHandle":encode(sh),"targetHandle":encode(th),"data":{"sourceHandle":sh,"targetHandle":th}}
    return {"name":"Fixture FAQ offline v1","description":"Formats an approved synthetic FAQ without calling a model.","data":{"nodes":[inlet,prompt,outlet],"edges":[edge(inlet,"message",prompt,"topic"),edge(prompt,"prompt",outlet,"input_value")],"viewport":{"x":0,"y":0,"zoom":1}}}

if __name__=="__main__":
    import sysconfig
    path=pathlib.Path(sysconfig.get_paths()["purelib"])/"langflow/initial_setup/starter_projects/Basic Prompting.json"
    print(json.dumps(build(json.loads(path.read_text()))))
