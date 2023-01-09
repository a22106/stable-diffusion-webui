verification_email = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="X-UA-Compatible">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Document</title>
    <style>
        img{
            width: min(60%,100px);
        }
        .body__container{
            position: relative;
        }
        hr{
            margin: 0;
        }
    .body__container::after{
        width: 265px;
        height: 10px;
        background-color: #150050;
        position: absolute;
        top: -2px;
        left: 50%;
        transform: translateX(-50%);
        content: "";
    }
    .body__container{
        display: flex;
        justify-content: center;
        flex-direction: column;
        align-items: center;
        font-family: sans-serif;
    }
    h1{
        padding-top: 50px;
        font-size: 25px;
        padding-bottom: 20px;
        margin: 0;

    }
    .body__container p{
        text-align: center;
        color: #7a7a7a;
        font-size: 20px;
        margin: 0;padding: 0;
    }
    .body__description{
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 15px;
        padding-bottom: 40px;
    }

    #code{
        color: #fff;
        font-size: 50px;
        padding: 20px 160px;
        background-color: #150050;
    }
    footer{
        font-family: sans-serif;
        width: 100%;
        background-color: #150050;
        color: #fff;
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
        position: absolute;
        bottom: 0;
        left: 0;
    }
    a{
        color: #fff;
    }
    @media (max-width:600px) {
        #code{
        color: #fff;
        font-size: 50px;
        text-align: center;
        padding: 10px 80px;
        background-color: #150050;
    }
        footer{
            font-size: 12px;
        }
        
    }
    </style>
</head>
<body>
    <div style="width: 100%;display: flex;justify-content: center;padding: 10px 0;" id="logo__container">
        <img src="./html/logo.png" alt="">
       
    </div> <hr>
    <div class="body__container">
        <h1>Hello <span id="user">Pius Hwang</span>.</h1>
       <div class="body__description">
        <p>Thank you for using the imezy.</p>
        <p>Enter the authentication code below to verify your account.</p>
       </div>
        <h1 id="code">230109</h1>
    </div>
    <footer>
        <div>
            <p>If this is not the account you registered with yourself, please report it.</p>
            <a href="#" style="display: block;">Report</a>
            <span>Ⓒ Aivill Co. all rights reserved</span>
            
        </div>
    </footer>
    
</body>
</html>
"""