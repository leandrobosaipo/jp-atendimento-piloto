// Entirely generated benchmark data. No operational/customer values are copied.
export function syntheticHistory(){
 return Array.from({length:8000},(_,i)=>({id:`fixture-${i}`,type:'fixture',status:'complete',started:'2030-01-01T10:00:00Z',finished:'2030-01-01T10:00:05Z',counts:{read:50,updated:0,retained:400},differences:Array.from({length:13},(_,j)=>({id:String(i*13+j),kind:'changed',fields:[{field:'active',before:true,after:false},{field:'status',before:'scheduled',after:'confirmed'}]}))}));
}
