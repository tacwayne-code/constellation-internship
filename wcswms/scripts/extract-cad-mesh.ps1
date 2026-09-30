param([string]$Root = (Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference='Stop'
Add-Type -Path 'D:\SW2022\SOLIDWORKS\api\redist\SolidWorks.Interop.sldworks.dll'
$sw=New-Object -ComObject SldWorks.Application
$api=[SolidWorks.Interop.sldworks.ISldWorks]
$argsOpen=[object[]]@([string](Join-Path $Root '结构\样板间总装.SLDASM'),2,3,'',0,0)
$doc=$api.GetMethod('OpenDoc6').Invoke($sw,$argsOpen)
if($null -eq $doc){throw 'Assembly unavailable'}
$all=[SolidWorks.Interop.sldworks.IAssemblyDoc].GetMethod('GetComponents').Invoke($doc,@($false))
$capi=[SolidWorks.Interop.sldworks.IComponent2]
$out=Join-Path $Root 'data\cad-extract'
New-Item -ItemType Directory -Path $out -Force | Out-Null
$cache=@{};$instances=[System.Collections.Generic.List[object]]::new();$missing=[System.Collections.Generic.List[string]]::new()
foreach($c in $all){
  $path=[string]$capi.GetMethod('GetPathName').Invoke($c,@())
  if(-not $path.EndsWith('.SLDPRT',[StringComparison]::OrdinalIgnoreCase)){continue}
  $name=[string]$capi.GetProperty('Name2').GetValue($c)
  $cfg=[string]$capi.GetProperty('ReferencedConfiguration').GetValue($c)
  $key=$path+'|'+$cfg
  if(-not $cache.ContainsKey($key)){
    $tri=$capi.GetMethod('GetTessTriangles').Invoke($c,@($true))
    if($null -eq $tri){
      $part=$capi.GetMethod('GetModelDoc2').Invoke($c,@())
      if($null -ne $part){$tri=[SolidWorks.Interop.sldworks.IPartDoc].GetMethod('GetTessTriangles').Invoke($part,@($true))}
    }
    if($null -eq $tri -or $tri.Length -eq 0){
      $parts=[System.Collections.Generic.List[single]]::new()
      $part=$capi.GetMethod('GetModelDoc2').Invoke($c,@())
      if($null -ne $part){
        $bodies=[SolidWorks.Interop.sldworks.IPartDoc].GetMethod('GetBodies2').Invoke($part,@(0,$false))
        foreach($body in $bodies){
          $faces=[SolidWorks.Interop.sldworks.IBody2].GetMethod('GetFaces').Invoke($body,@())
          foreach($face in $faces){
            $faceTri=[SolidWorks.Interop.sldworks.IFace2].GetMethod('GetTessTriangles').Invoke($face,@($true))
            if($null -ne $faceTri){$parts.AddRange([single[]]$faceTri)}
          }
        }
      }
      $tri=$parts.ToArray()
    }
    if($null -eq $tri -or $tri.Length -eq 0){$missing.Add($name);continue}
    $mesh='mesh-'+$cache.Count+'.bin'
    $bytes=[byte[]]::new($tri.Length*4)
    [Buffer]::BlockCopy([single[]]$tri,0,$bytes,0,$bytes.Length)
    [IO.File]::WriteAllBytes((Join-Path $out $mesh),$bytes)
    $cache[$key]=$mesh
  }
  $transform=$capi.GetProperty('Transform2').GetValue($c)
  if($null -eq $transform){$missing.Add($name);continue}
  $matrix=[SolidWorks.Interop.sldworks.IMathTransform].GetProperty('ArrayData').GetValue($transform)
  $instances.Add([pscustomobject]@{name=$name;file=[IO.Path]::GetFileName($path);mesh=$cache[$key];transform=$matrix})
}
[pscustomobject]@{instances=$instances;missing=$missing;unique_meshes=$cache.Count;component_count=$all.Length} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $out 'scene.json') -Encoding utf8
[pscustomobject]@{instances=$instances.Count;missing=$missing.Count;unique_meshes=$cache.Count;components=$all.Length} | ConvertTo-Json

